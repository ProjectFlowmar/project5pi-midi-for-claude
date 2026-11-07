; Inno Setup script for P5 MIDI Studio. Built by packaging/build.py.
#define AppVersion GetEnv("P5MIDI_VERSION")
[Setup]
AppId={{6E0C3D3A-5B7E-4F4E-9A51-5F1D8C2B7A10}
AppName=P5 MIDI Studio
AppVersion={#AppVersion}
AppPublisher=Project5pi
AppPublisherURL=https://project5pi.com/midi/
AppSupportURL=https://github.com/ProjectFlowmar/project5pi-midi-for-claude
DefaultDirName={localappdata}\Programs\P5 MIDI Studio
DefaultGroupName=P5 MIDI Studio
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=P5-MIDI-Studio-Setup-{#AppVersion}
SetupIconFile=p5midi.ico
UninstallDisplayIcon={app}\P5 MIDI Studio.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\build\pyi\dist\P5 MIDI Studio\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\P5 MIDI Studio"; Filename: "{app}\P5 MIDI Studio.exe"
Name: "{group}\Uninstall P5 MIDI Studio"; Filename: "{uninstallexe}"
Name: "{userdesktop}\P5 MIDI Studio"; Filename: "{app}\P5 MIDI Studio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\P5 MIDI Studio.exe"; Description: "Open P5 MIDI Studio now"; Flags: nowait postinstall skipifsilent
