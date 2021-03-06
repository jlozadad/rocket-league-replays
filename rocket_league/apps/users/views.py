import base64
import datetime
import re
import xml.etree.ElementTree as ET

import requests
from braces.views import LoginRequiredMixin
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.messages.views import SuccessMessageMixin
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now
from django.views.generic import (DetailView, FormView, TemplateView,
                                  UpdateView, View)
from rest_framework import views
from rest_framework.response import Response
from social.apps.django_app.default.models import UserSocialAuth
from social.backends.steam import USER_INFO

from ..replays.models import PRIVACY_PUBLIC, Replay
from .forms import PatreonSettingsForm, StreamSettingsForm, UserSettingsForm
from .models import Profile, SteamCache
from .serializers import StreamDataSerializer


class UserSettingsView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    template_name = 'users/user_settings.html'
    model = User
    form_class = UserSettingsForm
    success_message = "Your settings were successfully updated."

    def get_success_url(self):
        return reverse('users:settings')

    def get_object(self):
        return self.request.user


class SettingsView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    template_name = 'users/patreon_settings.html'
    model = Profile
    form_class = PatreonSettingsForm
    success_message = ("Your settings were successfully updated. If you are a "
                       "new patron it may take a couple of hours for your "
                       "benefits to be applied. Please get in touch if they're "
                       "not applied after 24 hours.")

    def get_success_url(self):
        return reverse('users:settings')

    def get_object(self):
        return self.request.user.profile

    def get_form_kwargs(self):
        kwargs = super(SettingsView, self).get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)

        if 'username' in form.changed_data:
            self.request.user.username = form.cleaned_data['username']
            self.request.user.save()

        return response


class PublicProfileView(DetailView):
    model = User
    slug_url_kwarg = 'username'
    slug_field = 'username'
    template_name = 'users/user_public_profile.html'
    context_object_name = 'public_user'

    def get(self, request, *args, **kwargs):
        response = super(PublicProfileView, self).get(request, *args, **kwargs)

        if self.object.profile.has_steam_connected():
            return redirect('users:steam', steam_id=self.object.profile.steam_info()['steamid'])

        return response


class SteamView(TemplateView):
    template_name = 'users/steam_profile.html'

    def get(self, request, *args, **kwargs):
        if not kwargs['steam_id'].isnumeric():
            # Try to get the 64 bit ID for a user.
            try:
                data = requests.get('http://steamcommunity.com/id/{}/?xml=1'.format(
                    kwargs['steam_id']
                ))

                xml = ET.fromstring(data.text)

                kwargs['steam_id'] = xml.findall('steamID64')[0].text
                return redirect('users:steam', steam_id=kwargs['steam_id'])
            except Exception as e:
                raise Http404(e)

        return super(SteamView, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(SteamView, self).get_context_data(**kwargs)

        # Is this Steam ID associated with a user?
        try:
            social_obj = UserSocialAuth.objects.get(
                uid=kwargs['steam_id'],
            )
            context['steam_info'] = social_obj.extra_data['player']

            context['uploaded'] = social_obj.user.replay_set.all()

            # Limit to public games, or unlisted / private games uploaded by the user.
            if self.request.user.is_authenticated() and self.request.user == social_obj.user:
                context['uploaded'] = context['uploaded'].filter(
                    Q(privacy=PRIVACY_PUBLIC) | Q(user=self.request.user)
                )
            else:
                context['uploaded'] = context['uploaded'].filter(
                    privacy=PRIVACY_PUBLIC,
                )

            context['has_user'] = True
            context['social_obj'] = social_obj
        except UserSocialAuth.DoesNotExist:
            # Pull the profile data and pass it in.
            context['has_user'] = False
            context['steam_info'] = None

            # Do we have a cache object for this already?
            try:
                cache = SteamCache.objects.filter(
                    uid=kwargs['steam_id']
                )

                if cache.count() > 0:
                    for cache_item in cache[1:]:
                        cache_item.delete()

                    cache = cache[0]

                    # Have we updated this profile recently?
                    if 'last_updated' in cache.extra_data:
                        # Parse the last updated date.
                        last_date = parse_datetime(cache.extra_data['last_updated'])

                        seconds_ago = (now() - last_date).seconds

                        # 3600  seconds = 1 hour
                        # 21600 seconds = 6 hours
                        if seconds_ago < 21600:
                            context['steam_info'] = cache.extra_data['player']

            except SteamCache.DoesNotExist:
                pass

            try:
                if not context['steam_info']:
                    player = requests.get(USER_INFO, params={
                        'key': settings.SOCIAL_AUTH_STEAM_API_KEY,
                        'steamids': kwargs['steam_id'],
                    }).json()

                    if len(player['response']['players']) > 0:
                        context['steam_info'] = player['response']['players'][0]

                        # Store this data in a SteamCache object.
                        cache_obj, _ = SteamCache.objects.get_or_create(
                            uid=kwargs['steam_id']
                        )
                        cache_obj.extra_data = {
                            'player': context['steam_info'],
                            'last_updated': now().isoformat(),
                        }
                        cache_obj.save()
            except:
                pass

        context['appears_in'] = Replay.objects.filter(
            show_leaderboard=True,
            player__platform__in=['OnlinePlatform_Steam', '1'],
            player__online_id=kwargs['steam_id'],
        ).distinct()

        if self.request.user.is_authenticated():
            context['appears_in'] = context['appears_in'].filter(
                Q(privacy=PRIVACY_PUBLIC) | Q(user=self.request.user)
            )
        else:
            context['appears_in'] = context['appears_in'].filter(
                privacy=PRIVACY_PUBLIC,
            )

        if not context.get('steam_info', None):
            context['steam_info'] = {
                'steamid': kwargs['steam_id'],
            }

        return context


class StreamDataView(View):

    def get(self, request, *args, **kwargs):
        user = User.objects.get(pk=kwargs['user_id'])
        context = user.profile.stream_settings
        context['user'] = user

        if kwargs['method'] == 'single':
            fields = ['show_wins', 'show_losses', 'show_average_goals',
                      'show_average_assists', 'show_average_saves',
                      'show_average_shots', 'show_games_played',
                      'show_win_percentage', 'show_goal_assist_ratio']

            for field in fields:
                context[field] = False

            context['show_{}'.format(kwargs['field'])] = True
        if kwargs['method'] == 'custom':
            template = base64.b64decode(kwargs['template']).decode('utf-8')
            context['template'] = re.sub(r'{([a-z_]+)}', r'{{ data.\1 }}', template)

        context['method'] = kwargs['method']
        context['field'] = kwargs.get('field', '')

        return render(request, 'users/stream_data.html', context=context)


class StreamSettingsView(LoginRequiredMixin, SuccessMessageMixin, FormView):
    model = Profile
    template_name = 'users/stream_settings.html'
    success_message = "Your settings were successfully updated."
    form_class = StreamSettingsForm

    def get_success_url(self):
        return reverse('users:stream_settings')

    def get_initial(self):
        settings = self.request.user.profile.stream_settings

        if settings == {}:
            # Set a sensible default.
            profile = Profile.objects.get(pk=self.request.user.profile.pk)
            profile.stream_settings = {
                'show_average_goals': True,
                'show_games_played': True,
                'background_color': '#00ff00',
                'limit_to': 'today',
                'show_win_percentage': True,
                'font_size': '16',
                'font': 'Arial',
                'text_color': '#ffffff',
                'show_goal_assist_ratio': True,
                'show_average_shots': True,
                'show_average_assists': True,
                'show_average_saves': True,
                'show_wins': True,
                'show_losses': True,
                'custom_font': '',
                'transparent_background': True,
                'text_shadow': True
            }

            profile.save()

            settings = profile.stream_settings

        return settings

    def get_form_kwargs(self):
        kwargs = super(StreamSettingsView, self).get_form_kwargs()
        kwargs['label_suffix'] = ''
        return kwargs

    def form_valid(self, form):
        # Set all boolean values to false.
        data = dict(form.cleaned_data)

        profile = Profile.objects.get(pk=self.request.user.profile.pk)
        profile.stream_settings = data
        profile.save()
        return super(StreamSettingsView, self).form_valid(form)


class StreamDataAPIView(views.APIView):

    serializer_class = StreamDataSerializer

    def get_serializer_context(self):
        user = get_object_or_404(User, pk=self.kwargs['user_id'])
        context = user.profile.stream_settings
        context['user'] = user

        # Data
        context['games_played'] = user.replay_set.all()
        context['wins'] = 0
        context['losses'] = 0

        context['average_goals'] = 0
        context['average_assists'] = 0
        context['average_saves'] = 0
        context['average_shots'] = 0
        context['win_percentage'] = 0
        context['goal_assist_ratio'] = 0

        goal_data = []
        assist_data = []
        save_data = []
        shot_data = []

        if context['limit_to'] == '3':
            context['games_played'] = context['games_played'][:3]
        elif context['limit_to'] == '5':
            context['games_played'] = context['games_played'][:5]
        elif context['limit_to'] == '10':
            context['games_played'] = context['games_played'][:10]
        elif context['limit_to'] == '20':
            context['games_played'] = context['games_played'][:20]
        elif context['limit_to'] == 'hour':
            context['games_played'] = context['games_played'].filter(
                timestamp__gte=now() - datetime.timedelta(hours=1)
            )
        elif context['limit_to'] == 'today':
            context['games_played'] = context['games_played'].filter(
                timestamp__gte=now() - datetime.timedelta(days=1)
            )

        elif context['limit_to'] == 'week':
            context['games_played'] = context['games_played'].filter(
                timestamp__gte=now() - datetime.timedelta(days=7)
            )
        elif context['limit_to'] == 'all':
            # We don't need to do anything here.
            pass
        # elif context['limit_to'] == 'session':
        #     pass

        # What team was the user on?
        uid = user.social_auth.get(provider='steam').uid

        for replay in context['games_played']:
            # Which team was this user on?
            player = replay.player_set.filter(
                platform__in=['OnlinePlatform_Steam', '1'],
                online_id=uid,
            )

            if player.count() == 0:
                continue

            player = player[0]

            if player.team == 0:
                if replay.team_0_score > replay.team_1_score:
                    context['wins'] += 1
                else:
                    context['losses'] += 1
            elif player.team == 1:
                if replay.team_1_score > replay.team_0_score:
                    context['wins'] += 1
                else:
                    context['losses'] += 1

            goal_data.append(player.goals)
            assist_data.append(player.assists)
            save_data.append(player.saves)
            shot_data.append(player.shots)

        context['games_played'] = context['games_played'].count()

        # Avoid dividing by zero.
        if len(goal_data) > 0:
            context['average_goals'] = "{0:.2f}".format(sum(goal_data) / len(goal_data))

        if len(assist_data) > 0:
            context['average_assists'] = "{0:.2f}".format(sum(assist_data) / len(assist_data))

        if len(save_data) > 0:
            context['average_saves'] = "{0:.2f}".format(sum(save_data) / len(save_data))

        if len(shot_data) > 0:
            context['average_shots'] = "{0:.2f}".format(sum(shot_data) / len(shot_data))

        if context['games_played'] > 0:
            context['win_percentage'] = "{0:.2f}".format(context['wins'] / context['games_played'] * 100)

        if sum(assist_data) > 0:
            context['goal_assist_ratio'] = "{0:.2f}".format(sum(goal_data) / sum(assist_data))
        else:
            context['goal_assist_ratio'] = sum(goal_data)

        return context

    def get(self, request, *args, **kwargs):
        serializer = self.serializer_class(self.get_serializer_context())
        return Response(serializer.data)
