from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds `number_quota` to Organisation:
    - number_quota: how many unique phone numbers the org is allowed to call.
    Default of 4 keeps existing orgs at the previously understood default.
    """

    dependencies = [
        ('aos_agent', '0011_alter_user_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='organisation',
            name='number_quota',
            field=models.PositiveIntegerField(
                default=4,
                help_text='Maximum unique phone numbers this organisation can call.',
            ),
        ),
    ]