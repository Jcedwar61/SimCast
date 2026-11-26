// The SimCast plugin for SimHub: SimCast's commentary booth as SimHub properties, and the booth's buttons as SimHub
// actions.
//
// SimCast serves its booth at http://127.0.0.1:8687/simhub/state (simcast/simhub.py). This plugin reads it five
// times a second on its own thread and publishes every value as a property, SimCastPlugin.<name> (the names and
// their defaults are in Keys.g.cs, generated from simhub.py by tools/simhub/build_simhub.py). The SimCast Booth
// dashboard is built on them, and any other dashboard can use them too.
//
// Actions (Controls and events, or anything else in SimHub that can run an action):
//   SimCastPlugin.SkipLine         stop the line being said
//   SimCastPlugin.ToggleOnAir      booth off air / back on air
//   SimCastPlugin.TalkAboutMe      talk about my race, now
//   SimCastPlugin.ToggleDriveMode  Driving <-> Stream
//   SimCastPlugin.ToggleNascar     NASCAR on/off for this race
//
// The port comes from SimCast's own settings (%APPDATA%\SimCast\config.json, "simhub_port"), so changing it in
// SimCast is all it takes. Nothing here runs on SimHub's game-data path: DataUpdate does nothing.
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using GameReaderCommon;
using SimHub.Plugins;

namespace SimCastForSimHub
{
    [PluginDescription("SimCast's live commentary booth on your dashboards: who's talking and what they're saying, " +
                       "on air or off, the booth's timing tower, and the booth's buttons as SimHub actions. " +
                       "Needs SimCast running on this PC.")]
    [PluginAuthor("Jcedwar61")]
    [PluginName("SimCast")]
    public class SimCastPlugin : IPlugin, IDataPlugin
    {
        const int DefaultPort = 8687;
        const int PollMs = 200;
        const int RetryMs = 1000;
        const int TimeoutMs = 1500;

        public PluginManager PluginManager { get; set; }

        volatile Dictionary<string, object> values;      // the latest snapshot; null while SimCast can't be reached
        volatile int port = DefaultPort;
        ManualResetEvent stop;
        Thread poller;

        public void Init(PluginManager pluginManager)
        {
            foreach (var kv in Keys.All)
            {
                string key = kv.Key;
                object fallback = kv.Value;
                this.AttachDelegate(key, () => Get(key, fallback));
            }
            this.AttachDelegate("Connected", () => values != null);
            this.AttachDelegate("Disconnected", () => values == null);
            this.AttachDelegate("Port", () => port);

            this.AddAction("SkipLine", (pm, name) => Send("skip"));
            this.AddAction("ToggleOnAir", (pm, name) => Send("mute"));
            this.AddAction("TalkAboutMe", (pm, name) => Send("me"));
            this.AddAction("ToggleDriveMode", (pm, name) => Send("mode"));
            this.AddAction("ToggleNascar", (pm, name) => Send("nascar"));

            port = ReadPort();
            stop = new ManualResetEvent(false);
            poller = new Thread(Run) { IsBackground = true, Name = "SimCast link" };
            poller.Start();
        }

        public void DataUpdate(PluginManager pluginManager, ref GameData data)
        {
        }

        public void End(PluginManager pluginManager)
        {
            if (stop != null)
                stop.Set();
            if (poller != null)
                poller.Join(2000);
            values = null;
        }

        object Get(string key, object fallback)
        {
            var d = values;
            object v;
            if (d != null && d.TryGetValue(key, out v) && v != null)
                return v;
            return fallback;
        }

        void Run()
        {
            var json = new JavaScriptSerializer { MaxJsonLength = 1 << 20 };
            int fails = 0;
            while (!stop.WaitOne(fails == 0 ? PollMs : RetryMs))
            {
                try
                {
                    var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + "/simhub/state");
                    req.Proxy = null;                       // straight to this PC, never through a system proxy
                    req.Timeout = TimeoutMs;
                    req.ReadWriteTimeout = TimeoutMs;
                    req.KeepAlive = false;
                    using (var resp = (HttpWebResponse)req.GetResponse())
                    using (var reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                    {
                        var d = json.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
                        values = d;
                        fails = 0;
                    }
                }
                catch (Exception)
                {
                    fails++;
                    if (fails >= 3)
                        values = null;                      // SimCast closed: the dashboard says so
                    if (fails % 5 == 1)
                        port = ReadPort();                  // the port may have been changed in SimCast
                }
            }
        }

        void Send(string action)
        {
            int p = port;
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + p + "/simhub/action/" + action);
                    req.Method = "POST";
                    req.Proxy = null;
                    req.Timeout = TimeoutMs;
                    req.KeepAlive = false;
                    req.ContentLength = 0;
                    req.Headers["X-SimCast"] = "SimHub";    // SimCast only takes button presses that carry this
                    using (req.GetResponse())
                    {
                    }
                }
                catch (Exception)
                {
                    // SimCast isn't running: nothing to press
                }
            });
        }

        static int ReadPort()
        {
            try
            {
                string appdata = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                string path = Path.Combine(Path.Combine(appdata, "SimCast"), "config.json");
                if (!File.Exists(path))
                    return DefaultPort;
                var cfg = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
                object v;
                if (cfg != null && cfg.TryGetValue("simhub_port", out v) && v != null)
                {
                    int p = Convert.ToInt32(v);
                    if (p > 0 && p < 65536)
                        return p;
                }
            }
            catch (Exception)
            {
            }
            return DefaultPort;
        }
    }
}
