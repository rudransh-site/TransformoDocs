"""V2 tests: real documents through the real pipeline with V2 switched on."""
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import pymupdf
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from core import constants as C
from core.models import Document, SystemSetting
from core.services import pipeline, reviews
from core.models import ReviewTask
from intelligence.models import ChatMessage, DocumentChunk, DocumentInsight, DocumentTag, Tag, Translation
from intelligence.services import ai_classify, chat, embeddings, enrich, nlp, summarize, tags, translate

User = get_user_model()
SAMPLES = Path(tempfile.mkdtemp(prefix="td_v2_samples_"))
MEDIA = tempfile.mkdtemp(prefix="td_v2_media_")


def setUpModule():
    call_command("make_sample_docs", out=str(SAMPLES), verbosity=0)


def tearDownModule():
    shutil.rmtree(SAMPLES, ignore_errors=True)
    shutil.rmtree(MEDIA, ignore_errors=True)


def sample(name):
    return SimpleUploadedFile(name, (SAMPLES / name).read_bytes())


def text_pdf(lines):
    doc = pymupdf.open()
    page = doc.new_page()
    y = 70
    for line in lines:
        page.insert_text((60, y), line, fontsize=11)
        y += 20
    data = doc.tobytes()
    doc.close()
    return data


@override_settings(MEDIA_ROOT=MEDIA)
class V2Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_initial_data", verbosity=0)

        def mk(name, role):
            u = User.objects.create_user(name, f"{name}@example.com", "Pass#12345")
            u.profile.role = role
            u.profile.save()
            return u

        cls.user, cls.other = mk("alice", C.Role.USER), mk("bob", C.Role.USER)
        cls.rev, cls.admin = mk("rev1", C.Role.REVIEWER), mk("boss", C.Role.ADMIN)

    def client_for(self, user):
        c = Client()
        c.force_login(user)
        return c

    def process(self, user, name, data=None):
        c = self.client_for(user)
        up = SimpleUploadedFile(name, data) if data is not None else sample(name)
        r = c.post("/api/documents/", {"file": up})
        self.assertEqual(r.status_code, 201, r.content)
        doc_id = r.json()["id"]
        self.assertEqual(c.post(f"/api/documents/{doc_id}/process/").status_code, 200)
        return c, Document.objects.get(pk=doc_id)


class EnrichmentTests(V2Base):
    def test_invoice_gets_summary_entities_tags_and_chunks(self):
        _c, doc = self.process(self.user, "invoice_native.pdf")
        v = doc.current_version
        self.assertEqual(doc.status, "VERIFIED")
        self.assertIn("INV-2026-0142", v.summary)
        self.assertIn("4130.00", v.summary)
        labels = {(e["label"], e["text"]) for e in v.entities}
        self.assertIn(("Date", "14/10/2026"), labels)
        self.assertIn(("Organization", "Acme Traders Pvt Ltd"), labels)
        names = {t.tag.name for t in DocumentTag.objects.filter(document=doc)}
        self.assertIn("Invoice", names)
        self.assertIn("2026", names)
        self.assertGreater(DocumentChunk.objects.filter(version=v).count(), 0)
        insight = DocumentInsight.objects.get(version=v)
        self.assertEqual(insight.classification_method, "RULES")
        self.assertEqual(insight.error, "")
        self.assertEqual(v.language, "en")

    def test_resume_and_certificate_summaries(self):
        _c, r = self.process(self.user, "resume_native.pdf")
        self.assertIn("Resume of RAHUL VERMA", r.current_version.summary)
        self.assertIn("rahul.verma@example.com", {e["text"] for e in r.current_version.entities if e["label"] == "Email"})
        _c, cert = self.process(self.user, "certificate_native.pdf")
        self.assertIn("awarded to Priya Sharma", cert.current_version.summary)

    def test_unknown_document_gets_extractive_summary(self):
        _c, doc = self.process(self.user, "review_unknown_letter.pdf")
        self.assertEqual(doc.status, "PENDING_VERIFICATION")
        self.assertIn("garden", doc.current_version.summary)

    def test_enrichment_failure_never_breaks_processing(self):
        with mock.patch("intelligence.services.enrich.enrich_version", side_effect=RuntimeError("boom")):
            _c, doc = self.process(self.user, "invoice_native.pdf")
        self.assertEqual(doc.status, "VERIFIED")
        self.assertFalse(DocumentInsight.objects.filter(version=doc.current_version).exists())

    def test_partial_failures_are_recorded_not_raised(self):
        with mock.patch("intelligence.services.embeddings.ensure_chunks", side_effect=RuntimeError("no model")):
            _c, doc = self.process(self.user, "invoice_native.pdf")
        insight = DocumentInsight.objects.get(version=doc.current_version)
        self.assertIn("embeddings: no model", insight.error)
        self.assertTrue(doc.current_version.summary)

    def test_regenerate_and_enrich_existing_command(self):
        _c, doc = self.process(self.user, "invoice_native.pdf")
        DocumentInsight.objects.all().delete()
        call_command("enrich_existing", verbosity=0)
        self.assertTrue(DocumentInsight.objects.filter(version=doc.current_version).exists())
        c = self.client_for(self.user)
        self.assertEqual(c.post(f"/api/documents/{doc.pk}/insights/").status_code, 200)
        self.assertEqual(self.client_for(self.other).post(f"/api/documents/{doc.pk}/insights/").status_code, 404)
        self.assertEqual(self.client_for(self.admin).post(f"/api/documents/{doc.pk}/insights/").status_code, 200)

    def test_detail_page_has_v2_tabs_and_chat_button(self):
        c, doc = self.process(self.user, "invoice_native.pdf")
        page = c.get(f"/documents/{doc.pk}/")
        for text in ("Summary", "Entities &amp; tags", "Translate", 'id="chat-fab"', "Automatic summary"):
            self.assertContains(page, text)
        pending = self.process(self.user, "review_unknown_letter.pdf")[1]
        self.assertContains(c.get(f"/documents/{pending.pk}/"), 'id="chat-fab"')

    def test_reviewer_edit_refreshes_summary(self):
        _c, doc = self.process(self.user, "review_invoice_missing_total.pdf")
        task = ReviewTask.objects.get(version=doc.current_version)
        reviews.claim(task.pk, self.rev)
        reviews.approve(task.pk, self.rev, {"invoice_number": "INV-2026-0142", "invoice_date": "14/10/2026",
                                            "total_amount": "4130"})
        doc.current_version.refresh_from_db()
        self.assertIn("total of 4130.00", doc.current_version.summary)


class ClassificationTests(V2Base):
    def test_ai_second_opinion_names_a_partial_invoice(self):
        pdf = text_pdf(["Sharma Electronics", "Bill to: Acme Traders Pvt Ltd, Indore", "Total payable 4130.00",
                        "GST 630.00", "Please pay before 28/10/2026", "Thank you for your business"])
        _c, doc = self.process(self.user, "partial.pdf", data=pdf)
        self.assertEqual(doc.doc_type.code, "INVOICE")
        self.assertEqual(DocumentInsight.objects.get(version=doc.current_version).classification_method, "AI")
        self.assertContains(self.client_for(self.user).get(f"/documents/{doc.pk}/"), "AI second-opinion")

    def test_ai_never_names_a_type_without_keyword_evidence(self):
        _c, doc = self.process(self.user, "review_unknown_letter.pdf")
        self.assertIsNone(doc.doc_type)
        self.assertEqual(DocumentInsight.objects.get(version=doc.current_version).classification_method, "RULES")

    def test_threshold_is_an_admin_setting(self):
        SystemSetting.set("AI_CLASSIFY_MIN_CONFIDENCE", 0.9)
        pdf = text_pdf(["Sharma Electronics", "Bill to: Acme Traders Pvt Ltd, Indore", "Total payable 4130.00",
                        "GST 630.00", "Please pay before 28/10/2026", "Thank you for your business"])
        _c, doc = self.process(self.user, "partial2.pdf", data=pdf)
        self.assertIsNone(doc.doc_type)
        with self.assertRaises(ValueError):
            SystemSetting.set("AI_CLASSIFY_MIN_CONFIDENCE", 5)


class SearchTests(V2Base):
    def setUp(self):
        self.inv = self.process(self.user, "invoice_native.pdf")[1]
        self.res = self.process(self.user, "resume_native.pdf")[1]
        self.process(self.other, "id_card_scanned.png")

    def test_embedder_falls_back_and_understands_synonyms(self):
        self.assertEqual(embeddings.active_backend(), "hash")
        v = embeddings.embed(["payment due", "balance outstanding", "garden pets", ""])
        self.assertGreater(float(v[0] @ v[1]), float(v[0] @ v[2]) + 0.15)
        self.assertEqual(float(v[3] @ v[3]), 0.0)  # empty text -> zero vector, no crash

    def test_semantic_search_finds_by_meaning_and_is_scoped(self):
        c = self.client_for(self.user)
        page = c.get("/search/?q=balance+outstanding&mode=semantic")
        self.assertContains(page, "invoice_native.pdf")
        self.assertContains(page, "ranked by meaning")
        self.assertNotContains(page, "id_card_scanned.png")  # belongs to another user
        self.assertContains(c.get("/search/?q=programming+skills&mode=semantic"), "resume_native.pdf")
        self.assertContains(c.get("/search/?q=zzzqqq+xxyy&mode=semantic"), "No documents matched")
        api = c.get("/api/search/?q=payment+due&mode=semantic").json()
        self.assertEqual(api["mode"], "semantic")
        self.assertEqual(api["results"][0]["filename"], "invoice_native.pdf")
        self.assertIn("score", api["results"][0])
        self.assertEqual(c.get("/api/search/?q=INV-2026").json()["count"], 1)  # keyword mode unchanged

    def test_keyword_search_still_default_and_toggle_present(self):
        page = self.client_for(self.user).get("/search/?q=sharma")
        self.assertContains(page, "invoice_native.pdf")
        self.assertContains(page, "Semantic (by meaning)")

    def test_search_by_tag(self):
        c = self.client_for(self.user)
        slug = DocumentTag.objects.filter(document=self.inv, tag__name="Invoice").first().tag.slug
        page = c.get(f"/search/?tag={slug}")
        self.assertContains(page, "invoice_native.pdf")
        self.assertNotContains(page, "resume_native.pdf")

    def test_chunks_rebuilt_when_backend_changes(self):
        v = self.inv.current_version
        DocumentChunk.objects.filter(version=v).update(backend="minilm")
        self.assertEqual(embeddings.ensure_chunks(v, "hash"), "hash")
        self.assertTrue(DocumentChunk.objects.filter(version=v, backend="hash").exists())
        self.assertFalse(DocumentChunk.objects.filter(version=v, backend="minilm").exists())


class ChatTests(V2Base):
    def setUp(self):
        self.c, self.doc = self.process(self.user, "invoice_native.pdf")
        self.url = f"/api/documents/{self.doc.pk}/chat/"

    def ask(self, q, client=None):
        r = (client or self.c).post(self.url, data=json.dumps({"question": q}), content_type="application/json")
        return r

    def test_field_questions(self):
        a = self.ask("What is the total?").json()
        self.assertEqual(a["mode"], "field")
        self.assertIn("4130.00", a["answer"])
        self.assertTrue(a["sources"])
        self.assertIn("INV-2026-0142", self.ask("invoice number").json()["answer"])
        self.assertIn("Sharma Electronics", self.ask("Who is the customer?").json()["answer"])
        self.assertIn("2026-10-28", self.ask("when is payment due").json()["answer"])

    def test_summary_entities_and_passages(self):
        self.assertEqual(self.ask("Summarize this document").json()["mode"], "summary")
        ents = self.ask("List the dates").json()
        self.assertEqual(ents["mode"], "entities")
        self.assertIn("14/10/2026", ents["answer"])
        p = self.ask("tell me about the wireless mouse").json()
        self.assertEqual(p["mode"], "passages")
        self.assertIn("<mark>", p["sources"][0]["html"])
        none = self.ask("what is the warranty period?").json()
        self.assertEqual(none["mode"], "none")
        self.assertIn("could not find", none["answer"])

    def test_history_clear_and_validation(self):
        self.ask("What is the total?")
        self.ask("invoice number")
        history = self.c.get(self.url).json()["messages"]
        self.assertEqual([m["question"] for m in history], ["What is the total?", "invoice number"])
        self.assertContains(self.c.get(f"/documents/{self.doc.pk}/"), "What is the total?")
        self.assertEqual(self.ask("").status_code, 400)
        self.assertEqual(self.ask("x" * 501).status_code, 400)
        self.assertEqual(self.c.delete(self.url).status_code, 200)
        self.assertEqual(ChatMessage.objects.filter(document=self.doc).count(), 0)

    def test_permissions(self):
        other = self.client_for(self.other)
        self.assertEqual(self.ask("total", other).status_code, 404)
        admin = self.client_for(self.admin)
        self.assertEqual(self.ask("total", admin).status_code, 200)
        self.assertEqual(admin.delete(self.url).status_code, 200)
        anon = Client().post(self.url, data="{}", content_type="application/json")
        self.assertEqual(anon.status_code, 401)

    def test_no_text_documents_cannot_chat(self):
        c, doc = self.process(self.user, "bad_blank.png")
        self.assertEqual(doc.status, "FAILED")
        r = c.post(f"/api/documents/{doc.pk}/chat/", data=json.dumps({"question": "hi"}), content_type="application/json")
        self.assertEqual(r.status_code, 409)
        self.assertNotContains(c.get(f"/documents/{doc.pk}/"), 'id="chat-fab"')


class TranslationTests(V2Base):
    def setUp(self):
        self.c, self.doc = self.process(self.user, "invoice_native.pdf")
        self.url = f"/api/documents/{self.doc.pk}/translate/"
        translate._pairs_cache = (0.0, None)

    def tearDown(self):
        translate._pairs_cache = (0.0, None)

    def test_unavailable_pack_gives_clear_message(self):
        r = self.c.post(self.url, data=json.dumps({"target": "hi"}), content_type="application/json")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["code"], "TRANSLATION_UNAVAILABLE")
        self.assertIn("download_models", r.json()["error"])
        self.assertContains(self.c.get(f"/documents/{self.doc.pk}/"), "translation pack is not installed")

    def test_translation_is_cached_and_limited(self):
        calls = []

        def fake(text, src, tgt):
            calls.append(len(text))
            return "[hi] " + text[:20]

        with mock.patch("intelligence.services.translate.installed_pairs", return_value={("en", "hi"), ("hi", "en")}), \
                mock.patch("intelligence.services.translate.translate_text", side_effect=fake):
            SystemSetting.set("TRANSLATION_MAX_CHARS", 200)
            r = self.c.post(self.url, data=json.dumps({"target": "hi"}), content_type="application/json").json()
            self.assertEqual(r["target"], "hi")
            self.assertTrue(r["text"].startswith("[hi]"))
            self.assertFalse(r["cached"])
            self.assertTrue(r["truncated"])
            self.assertLessEqual(calls[0], 200)
            again = self.c.post(self.url, data=json.dumps({"target": "hi"}), content_type="application/json").json()
            self.assertTrue(again["cached"])
            self.assertEqual(len(calls), 1)
            self.assertTrue(Translation.objects.filter(version=self.doc.current_version, target_lang="hi").exists())
            page = self.c.get(f"/documents/{self.doc.pk}/")
            self.assertContains(page, "Translate again")
            same = self.c.post(self.url, data=json.dumps({"target": "en"}), content_type="application/json")
            self.assertEqual(same.status_code, 503)  # document is already English
            bad = self.c.post(self.url, data=json.dumps({"target": "fr"}), content_type="application/json")
            self.assertEqual(bad.status_code, 400)

    def test_translate_text_requires_installed_pair(self):
        with self.assertRaises(translate.TranslationUnavailable):
            translate.translate_text("hello", "en", "hi")
        self.assertEqual(translate.status(), {"available": False, "pairs": []})


class TagTests(V2Base):
    def test_manual_tags_and_permissions(self):
        c, doc = self.process(self.user, "invoice_native.pdf")
        url = f"/api/documents/{doc.pk}/tags/"
        r = c.post(url, data=json.dumps({"name": "  Q4   Urgent "}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["slug"], "q4-urgent")
        self.assertEqual(c.post(url, data=json.dumps({"name": ""}), content_type="application/json").status_code, 400)
        tag = Tag.objects.get(slug="q4-urgent")
        self.assertEqual(DocumentTag.objects.get(document=doc, tag=tag).source, "MANUAL")
        enrich.enrich_version(doc.current_version, doc=doc)  # regenerating keeps manual tags
        self.assertTrue(DocumentTag.objects.filter(document=doc, tag=tag).exists())
        self.assertEqual(self.client_for(self.other).delete(f"{url}{tag.pk}/").status_code, 404)
        self.assertEqual(c.delete(f"{url}{tag.pk}/").status_code, 200)
        self.assertFalse(DocumentTag.objects.filter(document=doc, tag=tag).exists())


class UnitTests(TestCase):
    def test_entities_use_patterns_for_dates_amounts_and_contacts(self):
        text = "Invoice Date: 14/10/2026\nTotal Amount: Rs. 4,130.00\nEmail: a.b@example.com\nCall 98765 43210\nwww.example.com/x"
        ents, backend = nlp.extract_entities(text)
        got = {(e["label"], e["text"]) for e in ents}
        self.assertIn(("Date", "14/10/2026"), got)
        self.assertIn(("Amount", "Rs. 4,130.00"), got)
        self.assertIn(("Email", "a.b@example.com"), got)
        self.assertIn(("Phone", "98765 43210"), got)
        self.assertTrue(any(l == "Link" for l, _ in got))
        self.assertIn(backend, ("spacy", "regex"))

    def test_regex_fallback_when_spacy_missing(self):
        with mock.patch.object(nlp, "get_nlp", return_value=None):
            ents, backend = nlp.extract_entities("Paid 500.00 on 3 March 2026 to a@b.com")
        self.assertEqual(backend, "regex")
        self.assertTrue({"Date", "Amount", "Email"} <= {e["label"] for e in ents})

    def test_summary_helpers(self):
        self.assertEqual(summarize.headline("INVOICE", {}), "Invoice.")
        s, backend = summarize.make_summary("", {}, "")
        self.assertEqual(s, "")
        long = ". ".join(f"Sentence number {i} talks about invoices and payments in detail today" for i in range(8)) + "."
        text, backend = summarize.make_summary("", {}, long)
        self.assertIn(backend, ("sumy", "simple"))
        self.assertTrue(text)

    def test_tags_filter_generic_words(self):
        phrases = tags.key_phrases("invoice invoice amount amount garden garden pets pets budget budget")
        self.assertNotIn("invoice", phrases)
        self.assertIn("garden", phrases)
        out = tags.suggest_tags("Invoice", [{"label": "Organization", "text": "Acme Ltd", "count": 1},
                                           {"label": "Date", "text": "14 Oct 2026", "count": 1}], ["garden"], {})
        self.assertEqual(out[:3], ["Invoice", "2026", "Acme Ltd"])

    def test_chunking(self):
        text = "\n".join(f"line number {i} with some words" for i in range(60))
        chunks = embeddings.chunk_text(text)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c) < 700 for c in chunks))
        self.assertEqual(embeddings.chunk_text(""), [])
        self.assertEqual(embeddings.from_bytes(embeddings.to_bytes([1.0, 2.0])).tolist(), [1.0, 2.0])

    def test_language_detection(self):
        from intelligence.services.text import detect_language
        self.assertEqual(detect_language("Hello world invoice"), "en")
        self.assertEqual(detect_language("यह प्रमाणित किया जाता है कि"), "hi")
        self.assertEqual(detect_language(""), "en")
