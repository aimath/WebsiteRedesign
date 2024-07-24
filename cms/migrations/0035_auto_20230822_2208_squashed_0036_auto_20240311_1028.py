# Generated by Django 3.2.23 on 2024-03-11 09:32

from django.db import migrations, models


class Migration(migrations.Migration):

    replaces = [('cms', '0035_auto_20230822_2208'), ('cms', '0036_auto_20240311_1028')]

    dependencies = [
        ('cms', '0034_remove_pagecontent_placeholders'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='pagecontent',
            options={'default_permissions': [], 'verbose_name': 'page content', 'verbose_name_plural': 'page contents'},
        ),
        migrations.AlterModelOptions(
            name='page',
            options={'default_permissions': ('add', 'change', 'delete'), 'permissions': (('view_page', 'Can view page'), ('publish_page', 'Can publish page'), ('edit_static_placeholder', 'Can edit static placeholders')), 'verbose_name': 'page', 'verbose_name_plural': 'pages'},
        ),
    ]