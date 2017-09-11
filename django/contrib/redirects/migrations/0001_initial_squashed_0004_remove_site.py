from django.db import migrations, models


class Migration(migrations.Migration):

    replaces = [('redirects', '0001_initial'), ('redirects', '0002_add_domain'), ('redirects', '0003_migrate_sites'), ('redirects', '0004_remove_site')]

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Redirect',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('old_path', models.CharField(db_index=True, help_text="This should be an absolute path, excluding the domain name. Example: '/events/search/'.", max_length=200, verbose_name='redirect from')),
                ('new_path', models.CharField(blank=True, help_text="This can be either an absolute path (as above) or a full URL starting with 'http://'.", max_length=200, verbose_name='redirect to')),
                ('domain', models.CharField(blank=True, max_length=255)),
            ],
            options={
                'verbose_name_plural': 'redirects',
                'ordering': ('old_path',),
                'verbose_name': 'redirect',
                'unique_together': {('domain', 'old_path')},
                'db_table': 'django_redirect',
            },
        ),
    ]
