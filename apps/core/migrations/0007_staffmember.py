# Generated by Django 4.2.13 on 2024-07-23 22:18

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cms', '0037_alter_cmsplugin_id_alter_globalpagepermission_id_and_more'),
        ('core', '0006_delete_staffmember'),
    ]

    operations = [
        migrations.CreateModel(
            name='StaffMember',
            fields=[
                ('cmsplugin_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, related_name='%(app_label)s_%(class)s', serialize=False, to='cms.cmsplugin')),
                ('name', models.CharField(max_length=255)),
                ('role', models.CharField(max_length=255)),
                ('bio', models.TextField()),
                ('image', models.ImageField(upload_to='staff_images/')),
                ('more_info_link', models.URLField(blank=True, null=True)),
            ],
            bases=('cms.cmsplugin',),
        ),
    ]
