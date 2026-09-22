web: python manage.py migrate --noinput && python manage.py import_legacy_users && python manage.py delete_test_users && gunicorn core.wsgi:application --timeout 90
