SimCast for SimHub
==================

The SimCast Booth dashboard puts SimCast's commentary booth on any SimHub screen (a phone or tablet, a USB
display, a second monitor or an overlay), and the SimCast plugin gives SimHub the booth's five buttons.

What the dashboard shows
  Booth screen       ON AIR / OFF AIR, Driving or Stream, the coverage (whole field, your car, rally, NASCAR),
                     the AI in use; the race's name, the lap and the flag; who's talking, in their colour, and
                     what they're saying, live; the two lines before it; the booth's running order (the names the
                     booth uses, your car in red, battles in amber, the fastest lap in purple); your place, the
                     cars either side of you and the gaps; and your SimCast hotkeys.
  Transcript screen  the line being said and the six before it.
  Controls screen    each button, its SimCast hotkey and the SimHub action to bind it to.
  When SimCast isn't running, every screen says so.

Install (SimHub 9 on Windows)
  1. Close SimHub.
  2. Unzip SimCast-SimHub.zip, right-click Install-SimCastForSimHub.ps1 > Run with PowerShell. It finds SimHub,
     copies SimCastPlugin.dll into SimHub's folder and "SimCast Booth" into SimHub\DashTemplates. Windows asks for
     permission when SimHub is in Program Files.
     (By hand: copy SimCastPlugin.dll into SimHub's folder, next to SimHubWPF.exe, and the "SimCast Booth" folder
     into SimHub\DashTemplates.)
  3. Start SimHub and say yes when it asks to enable the new "SimCast" plugin.
  4. Start SimCast. Settings > Stream > SimHub is on by default (port 8687); it says when SimHub is reading it.
  5. SimHub > Dash Studio > SimCast Booth: show it on this PC, or on a phone or tablet through SimHub's web
     dashboards (http://<this PC>:8888).

The buttons
  In SimHub: Controls and events > add a mapping, choose one of these actions and press a wheel or button-box
  button:
    SimCastPlugin.SkipLine         stop the line being said
    SimCastPlugin.ToggleOnAir      booth off air / back on air
    SimCastPlugin.TalkAboutMe      the booth turns to your race, now
    SimCastPlugin.ToggleDriveMode  Driving (quiet while you're busy) / Stream (the full show)
    SimCastPlugin.ToggleNascar     NASCAR on/off for this race
  They do the same as SimCast's own hotkeys (Settings > Sims); use either or both.

Your own dashboards
  Every value is a SimHub property, SimCastPlugin.<name>: OnAir, Speaking, AirText, DriveMode, Coverage, Nascar,
  Title, LapText, FlagText, FlagColor, Line.Speaker, Line.Text, Line.Row1-4, Line.Color, History1-6.Speaker/.Text,
  Tower01-10.Pos/.Name/.Gap/.IsMe, Me.PosText, Me.Ahead, Me.GapAhead, Me.Behind, Me.GapBehind, Key.Skip,
  Connected and more (Settings > Available properties in SimHub, search "SimCast"). Text is already formatted and
  colours are #AARRGGBB, so a binding is just [SimCastPlugin.Line.Text].

Privacy
  The plugin only talks to SimCast on this PC (127.0.0.1). SimCast only accepts it from this PC, and only button
  presses that come from the plugin.
