# NOVA Auto Update и Inno Setup

## Собрать установщик вручную

1. Установи 64-битный Python 3.13 и Inno Setup 6.
2. Запусти `build/build_exe.bat`, чтобы собрать `dist/NOVA.exe`.
3. Запусти `powershell -ExecutionPolicy Bypass -File build/sync_version.ps1`, чтобы версия в Inno Setup совпала с `APP_VERSION` из `config.py`.
4. Открой `installer/NOVA.iss` в Inno Setup Compiler и нажми **Build → Compile** (или F9).
5. Проще запустить `build_installer.bat`: он собирает `NOVA.exe`, вызывает Inno Setup Compiler и кладёт файлы в `release/`:
   - `NOVA.exe` — приложение;
   - `NOVA_Setup_[APP_VERSION].exe` — установщик;
   - `latest.json` — манифест автообновления.

## Как сделать обновления для всех пользователей

Автообновление работает через один публичный GitHub Releases-канал. Все установленные копии NOVA обращаются к одному URL `latest.json`.

### Один раз — настрой репозиторий

1. Создай публичный GitHub-репозиторий, например `username/NOVA`, и загрузи туда весь проект.
2. В `config.py` замени:

   `UPDATE_MANIFEST_URL = "https://github.com/OWNER/NOVA/releases/latest/download/latest.json"`

   на URL своего репозитория, например:

   `UPDATE_MANIFEST_URL = "https://github.com/username/NOVA/releases/latest/download/latest.json"`

3. В `installer/NOVA.iss` замени ссылки `https://github.com/OWNER/NOVA` на адрес своего репозитория.
4. Для локальной сборки можешь перед запуском `build_installer.bat` задать `set NOVA_UPDATE_REPO=username/NOVA`. GitHub Actions выставляет эту переменную автоматически.
5. Собери и опубликуй первую версию. Именно эта сборка попадёт пользователям и будет знать, где искать будущие обновления.

### Как выпускать каждое следующее обновление

1. Измени код.
2. Увеличь `APP_VERSION` в `config.py`, например с `1.05.6` на `1.05.7`.
3. Версия Inno Setup автоматически синхронизируется из `APP_VERSION` скриптом `build/sync_version.ps1`; вручную менять `OutputBaseFilename` не нужно.
4. При необходимости обнови список изменений в `build/make_manifest.ps1`.
5. Отправь изменения в GitHub и создай тег той же версии:

   `git tag v1.05.7`

   `git push origin main --tags`

6. GitHub Actions автоматически соберёт Windows EXE и Inno Setup installer, вычислит SHA-256, создаст `latest.json` и опубликует GitHub Release.
7. При следующем запуске установленная NOVA проверит `latest.json`. Если версия новее, NOVA покажет уведомление и кнопку **«Скачать и установить обновление»** на странице «Обновления NOVA». После согласия NOVA скачает EXE, проверит SHA-256, запустит Inno Setup и закроется на время замены файлов.

### Что сохраняется при обновлении

Пользовательская база данных и настройки находятся в `%APPDATA%\NOVA`, отдельно от папки программы. Скрипт Inno Setup не удаляет эту папку, поэтому библиотека игр, профили, FPS History и настройки сохраняются.

`AppId` в `installer/NOVA.iss` должен оставаться неизменным для всех версий: так Inno Setup распознаёт существующую установку. Папку установки также следует сохранять прежней (`UsePreviousAppDir=yes`).

### Важно

- Репозиторий и `latest.json` должны быть доступны без авторизации. Для приватного репозитория понадобится отдельный сервер обновлений или авторизация; публичный токен в приложении хранить нельзя.
- Пользователям нужно сначала установить NOVA 1.05.6: более старые выпуски не содержат updater и не умеют сами скачать эту первую обновляющую сборку.
- Манифест и установщик скачиваются только по HTTPS. NOVA сверяет SHA-256 файла до запуска.
- Пользователь подтверждает установку; приложение не устанавливает обновление скрытно.
- Для более доверенной раздачи подпиши `NOVA.exe` и установщик Authenticode-сертификатом. SHA-256 защищает от повреждения/подмены файла относительно манифеста, но не заменяет цифровую подпись издателя.
- Сборка Windows EXE и Inno Setup делается в Windows или Windows GitHub Actions runner. Из Linux настоящий Windows installer не создаётся.
