// Reference-only declarations of the part of SimHub's plugin API the SimCast plugin uses, so it builds on a machine
// without SimHub (CI). Nothing here runs: at runtime SimHub's own SimHub.Plugins.dll is loaded in its place. It isn't
// strong-named, so the CLR binds it by simple name; that only works because every signature below matches SimHub's
// exactly (an extension call compiles to a static call on IPluginExtensions, so even that class's name matters).
// With SimHub installed, SimCastPlugin.csproj builds against the real assemblies instead.
using System;
using GameReaderCommon;

namespace SimHub.Plugins
{
    public class PluginManager
    {
        public object GetPropertyValue(string name) { return null; }
    }

    public interface IPlugin
    {
        PluginManager PluginManager { get; set; }
        void Init(PluginManager pluginManager);
        void End(PluginManager pluginManager);
    }

    public interface IDataPlugin
    {
        void DataUpdate(PluginManager pluginManager, ref GameData data);
    }

    [AttributeUsage(AttributeTargets.Class)]
    public class PluginNameAttribute : Attribute
    {
        public PluginNameAttribute(string name) { this.name = name; }
        public string name;
    }

    [AttributeUsage(AttributeTargets.Class)]
    public class PluginAuthorAttribute : Attribute
    {
        public PluginAuthorAttribute(string name) { this.name = name; }
        public string name;
    }

    [AttributeUsage(AttributeTargets.Class)]
    public class PluginDescriptionAttribute : Attribute
    {
        public PluginDescriptionAttribute(string description) { this.description = description; }
        public string description;
    }

    public class EventTrigger
    {
    }

    public static class IPluginExtensions
    {
        public static void AttachDelegate<T, U>(this T plugin, string name, Func<U> valueProvider)
            where T : IPlugin
        {
        }

        public static void AddAction<T>(this T plugin, string actionName, Action<PluginManager, string> actionStart,
                                        Action<PluginManager, string> actionEnd = null)
            where T : IPlugin
        {
        }
    }
}
