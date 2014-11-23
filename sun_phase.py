#!/usr/bin/python

from alfred_weather import WeatherWorkflow
from jcalfred import Item
from datetime import date

ICON_NAME=u"clear"
TODAY = u"today"
TOMORROW = u"tomorrow"
TIME_FORMAT=u"%H:%M"
class SunPhaseWorkflow(WeatherWorkflow):

    def _sun_phase_description(self, sunrise, sunset):
        content = ""
        if sunrise:
            content += u"Sunrise: {}".format(sunrise.strftime(TIME_FORMAT))
        if sunset:
            content += u", Sunset: {}".format(sunset.strftime(TIME_FORMAT))
        return content

    def _create_item(self, day_desc, content):
        title = u'{}: {}'.format(day_desc, content)
        icon = self._get_icon(ICON_NAME)
        return Item(title, icon=icon, valid=False)


    def tell_sun(self, location):
        """Tell sunrise and sunset time for today and following few days"""
        location = location.strip()
        weather = self._get_weather(location)

        items = self._show_alert_information(weather)

        days = self._get_days(weather)

        for day in days:
            day_desc = self._get_day_desc(day['date'])

            content = self._sun_phase_description(day.get('sunrise'), day.get('sunset'))

            if content == "":
                continue

            items.append(self._create_item(day_desc, content))
        items.append(self._get_copyright_info(weather))
        return items



if __name__=="__main__":
    import sys
    ap = SunPhaseWorkflow()
    ap.tell('sun', sys.argv[1] if len(sys.argv) > 1 else '')
