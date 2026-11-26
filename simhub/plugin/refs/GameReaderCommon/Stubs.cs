// Reference-only: the one GameReaderCommon type the plugin names (IDataPlugin.DataUpdate's parameter). SimHub's own
// GameReaderCommon.dll is the one loaded at runtime. See ../SimHub.Plugins/Stubs.cs.
namespace GameReaderCommon
{
    public class GameData
    {
        public bool GameRunning { get; set; }
    }
}
