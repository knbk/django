from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('redirects', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='redirect',
            name='domain',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
