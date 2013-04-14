[Alfred 2][alfred] Workflow for showing weather forecasts
=========================================================

<p align="center">
<img alt="Screenshot" src="http://i.imgur.com/Qg71xWm.png" />
</p>

This workflow lets you access weather forecasts from the [Weather
Underground][wund].  There are several setup commands, accessible as `wset
<command>`, and a single `weather` command to display current conditions and a
4-day forecast.

The setup commands are:

  * `key <your key>` - set your API key (described below)
  * `location <ZIP or city>` - set your default location
  * `icons` - choose an icon set
  * `units` - set your preferred unit system

The `weather` command, with no argument, will show information for your default
location. It can also be given a location, such as a ZIP code or city name. It
(and the `wset location` command) uses the Weather Underground autocomplete API
to find possible locations based on what you enter, and it just picks the first
one.

The first time you try to access the weather, you'll be asked to add a Weather
Underground API key. You can create an account and get a key from
[wunderground.com][api]. Both the account and API access are free, so long as
you sign up for a "developer" key. You'll also need to set a default location.

The data for each city you query is cached for 5 minutes to keep requests down
to a reasonable level while you're playing around with the workflow. The free
tier of Weather Underground API access only allows 10 requests per minute, and
it's surprisingly easy to hit that limit (you know, when you're spastically
querying city after city because using an Alfred workflow is just so cool).

Installation
------------

The easiest way to install the workflow is to download the
[prepackaged workflow][package].  Double-click on the downloaded file, or drag
it into the Alfred Workflows window, and Alfred should install it.

I'm using `weather` as the main command, which is the same as the built-in
weather web search in Alfred. The web search can be disabled in Features &rarr;
Web Search if you don't want it showing up in your weather report.
Alternatively, you can change the `weather` command to something else.

Requirements
------------

The only requirements are:

  * Python 2.7+
  * `requests`

If you have Lion or Mountain Lion, the [prepackaged workflow][package] includes
everything you need.

Credits
-------

This script was originally based on David Ferguson's Weather workflow. My code
base has diverged pretty far at this point, though, both in the source and in
how it works.

The package includes a number of icon sets from the Weather Underground and
from [weathericonsets.com][icons] (I'm not up to drawing weather icons yet).
Each set includes an `info.json` file that gives a short description and
provides a source URL for the icon set.

[api]: http://www.wunderground.com/weather/api/
[package]: https://dl.dropbox.com/s/hug7tz83dk5wsa5/jc-weather.alfredworkflow
[alfred]: http://www.alfredapp.com
[icons]: http://www.weathericonsets.com
[wund]: http://www.weatherunderground.com
