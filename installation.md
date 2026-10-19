Вот команды (PowerShell, из корня репозитория `C:\Users\sicke\PycharmProjects\CLI-code-agent`).

## 1. Только собрать zip
```powershell
python tools/build_addon.py
```
Результат: `dist\asset_assistant-0.4.0.zip`. С авто-валидацией:
```powershell
$env:BLENDER_BIN = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
python tools/build_addon.py
```

## 2. Собрать + установить + открыть Blender (одним блоком)
> Важно: **Blender должен быть закрыт** во время установки, иначе `[WinError 5] Access is denied`.

```powershell
# 0) убедиться, что Blender закрыт
Get-Process blender -ErrorAction SilentlyContinue

# 1) собрать
python tools/build_addon.py

# 2) установить свежий zip (имя берём автоматически)
$zip = (Get-ChildItem dist\asset_assistant-*.zip | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --command extension install-file -r user_default -e $zip

# 3) запустить Blender для просмотра
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
```

## 3. Где смотреть в Blender
- Чат: **3D-вьюпорт → `N` → вкладка «AI»** (также Shader Editor и Image Editor), либо `Ctrl+Alt+A` для плавающего окна.
- Настройки: **Edit → Preferences → Add-ons → Asset Assistant** — язык RU/EN, ширина окна, бэкенд (Ollama/API).
- Быстрая проверка версии/установки без GUI:
```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --command extension list | Select-String asset_assistant
```

Если после установки хочется просто переустановить при уже открытом Blender — сначала закройте его, затем повторите блок 2.