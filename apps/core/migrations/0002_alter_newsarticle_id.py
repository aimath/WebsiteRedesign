# Generated by Django 5.0.8 on 2025-04-03 20:19

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='newsarticle',
            name='id',
            field=models.AutoField(primary_key=True, serialize=False),
        ),
    ]
