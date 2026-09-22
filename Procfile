web: python manage.py migrate --noinput && python manage.py import_legacy_users && gunicorn core.wsgi:application --timeout 90
