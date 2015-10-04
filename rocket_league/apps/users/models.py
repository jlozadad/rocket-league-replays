from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Max
from django.utils.timezone import now

from datetime import timedelta


class Profile(models.Model):
    user = models.OneToOneField(User)

    def latest_ratings(self):
        ratings = self.user.leaguerating_set.all()[:1]

        if ratings:
            return {
                settings.PLAYLISTS['RankedDuels']: ratings[0].duels,
                settings.PLAYLISTS['RankedDoubles']: ratings[0].doubles,
                settings.PLAYLISTS['RankedSoloStandard']: ratings[0].solo_standard,
                settings.PLAYLISTS['RankedStandard']: ratings[0].standard,
            }

    def rating_diff(self):
        # Do we have ratings from today as well as yesterday?
        yesterdays_rating = self.user.leaguerating_set.filter(
            timestamp__startswith=now().date() - timedelta(days=1),
        ).aggregate(
            Max('duels'),
            Max('doubles'),
            Max('solo_standard'),
            Max('standard'),
        )

        todays_ratings = self.user.leaguerating_set.filter(
            timestamp__startswith=now().date(),
        ).aggregate(
            Max('duels'),
            Max('doubles'),
            Max('solo_standard'),
            Max('standard'),
        )

        response = {}

        if todays_ratings['duels__max'] and yesterdays_rating['duels__max']:
            response[settings.PLAYLISTS['RankedDuels']] = todays_ratings['duels__max'] - yesterdays_rating['duels__max']

        if todays_ratings['doubles__max'] and yesterdays_rating['doubles__max']:
            response[settings.PLAYLISTS['RankedDoubles']] = todays_ratings['doubles__max'] - yesterdays_rating['doubles__max']

        if todays_ratings['solo_standard__max'] and yesterdays_rating['solo_standard__max']:
            response[settings.PLAYLISTS['RankedSoloStandard']] = todays_ratings['solo_standard__max'] - yesterdays_rating['solo_standard__max']

        if todays_ratings['standard__max'] and yesterdays_rating['standard__max']:
            response[settings.PLAYLISTS['RankedStandard']] = todays_ratings['standard__max'] - yesterdays_rating['standard__max']

        return response


class LeagueRating(models.Model):

    user = models.ForeignKey(User)

    duels = models.PositiveIntegerField()

    doubles = models.PositiveIntegerField()

    solo_standard = models.PositiveIntegerField()

    standard = models.PositiveIntegerField()

    timestamp = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ['-timestamp']