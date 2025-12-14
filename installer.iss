[Setup]
AppName=kal
AppVersion=1.0.0
AppPublisher=sugoma
DefaultDirName={autopf}\kal
DefaultGroupName=kal
OutputDir=installer
OutputBaseFilename=kal_setup
Compression=lzma2/max
SolidCompression=yes
SetupIconFile=Sprite-0001.ico
UninstallDisplayIcon={app}\kal.exe
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
DisableProgramGroupPage=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\Kal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\kal"; Filename: "{app}\kal.exe"
Name: "{group}\Удалить kal"; Filename: "{uninstallexe}"
Name: "{autodesktop}\kal"; Filename: "{app}\kal.exe"; Tasks: desktopicon
Name: "{userstartup}\kal"; Filename: "{app}\kal.exe"; Tasks: startupicon

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные значки:"
Name: "startupicon"; Description: "Запускать при старте Windows"; GroupDescription: "Автозапуск:"

[Run]
Filename: "{app}\kal.exe"; Description: "Запустить kal"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
