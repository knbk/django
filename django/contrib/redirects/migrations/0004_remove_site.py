from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('redirects', '0003_migrate_sites'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='redirect',
            unique_together={('domain', 'old_path')},
        ),
        migrations.RemoveField(
            model_name='redirect',
            name='site',
        ),
    ]
