from django.db import migrations


def hide(apps, schema_editor):
    # Mining companies register and sign in on the Miner Portal, so the
    # compliance app's mining onboarding shows only the compliance partner
    # side. The rows stay, switched off, so an admin can turn them back on.
    OnboardingDashboard = apps.get_model("onboarding", "OnboardingDashboard")
    OnboardingDashboard.objects.filter(sector="mining", key="applicant").update(enabled=False)


def show(apps, schema_editor):
    OnboardingDashboard = apps.get_model("onboarding", "OnboardingDashboard")
    OnboardingDashboard.objects.filter(sector="mining", key="applicant").update(enabled=True)


class Migration(migrations.Migration):
    dependencies = [("onboarding", "0001_initial")]

    operations = [migrations.RunPython(hide, show)]
