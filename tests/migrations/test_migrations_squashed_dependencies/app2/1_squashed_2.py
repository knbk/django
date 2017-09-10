from django.db import migrations


class Migration(migrations.Migration):

    replaces = [
        ("app2", "1_auto"),
        ("app2", "2_auto"),
    ]

    dependencies = []

    operations = [
        migrations.RunPython(migrations.RunPython.noop)
    ]
