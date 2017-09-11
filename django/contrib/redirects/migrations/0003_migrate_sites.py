from django.db import migrations


def migrate_site_to_domain(apps, schema_editor):
    Site = apps.get_model('sites', 'Site')
    Redirect = apps.get_model('redirects', 'Redirect')

    db_alias = schema_editor.connection.alias
    for redirect in Redirect.objects.using(db_alias).all():
        site = Site.objects.using(db_alias).get(id=redirect.site_id)
        redirect.domain = site.domain
        redirect.save(update_fields=['domain'])


def migrate_domain_to_site(apps, schema_editor):
    Site = apps.get_model('sites', 'Site')
    Redirect = apps.get_model('redirects', 'Redirect')

    db_alias = schema_editor.connection.alias
    for redirect in Redirect.objects.using(db_alias).all():
        site, created = Site.objects.using(db_alias).get_or_create(domain=redirect.domain)
        redirect.site = site
        redirect.save(update_fields=['site'])


class Migration(migrations.Migration):

    dependencies = [
        ('redirects', '0002_add_domain'),
        ('sites', '0002_alter_domain_unique'),
    ]

    operations = [
        migrations.RunPython(migrate_site_to_domain, migrate_domain_to_site, elidable=True),
    ]
