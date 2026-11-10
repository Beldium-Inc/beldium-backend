from django.db import migrations


def hide(apps, schema_editor):
    # The compliance app's onboarding screen is for compliance partners and
    # regulators only. Each sector's own companies (miners, hauliers,
    # processors, exporters, buyers) do not sign up here, so the applicant
    # side is switched off in every sector. The rows stay so an admin can
    # switch a sector back on in Django admin.
    OnboardingDashboard = apps.get_model("onboarding", "OnboardingDashboard")
    OnboardingDashboard.objects.filter(key="applicant").update(enabled=False)


def show(apps, schema_editor):
    OnboardingDashboard = apps.get_model("onboarding", "OnboardingDashboard")
    OnboardingDashboard.objects.filter(key="applicant").update(enabled=True)


class Migration(migrations.Migration):
    dependencies = [("onboarding", "0001_initial")]

    operations = [migrations.RunPython(hide, show)]
