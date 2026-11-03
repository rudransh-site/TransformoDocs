# TransformoDocs - V1 Part 1 setup

## 1. Python environment (Python 3.11 or 3.12)
Windows (PowerShell):   python -m venv venv ; venv\Scripts\activate
Linux:                  python3 -m venv venv && source venv/bin/activate
Then:                   pip install -r requirements.txt
Optional (faster MySQL driver):  pip install mysqlclient   (if it fails, ignore: PyMySQL is used)

## 2. MySQL
Linux:   sudo apt install -y mysql-server libmysqlclient-dev pkg-config build-essential python3-dev
Windows: install MySQL Installer (or: winget install Oracle.MySQL), keep the service on auto-start.
Create the database (MySQL 8 recommended):
  CREATE DATABASE transformodocs CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

## 3. Config
Windows: copy .env.example .env      Linux: cp .env.example .env
Edit .env: DB_USER, DB_PASSWORD, DJANGO_SECRET_KEY.

## 4. Create tables + seed
python manage.py check
python manage.py migrate
python manage.py seed_initial_data      # 4 document types + default settings (safe to re-run)
python manage.py create_demo_users      # demo_user / demo_reviewer / demo_admin, password Demo@12345

## 5. Verify
python manage.py runserver
Open http://127.0.0.1:8000/admin/ -> log in as demo_admin.
You should see: 4 Document types, 10 System settings, 3 Profiles (USER, REVIEWER, ADMIN).

## Optional quick test (no MySQL needed)
Windows: set USE_SQLITE=1 ; python manage.py test core
Linux:   USE_SQLITE=1 python manage.py test core
