# discord-url

Монитор доступности Discord invite/vanity кодов. Присылает алерт в Telegram, когда код освобождается.

## Установка

```bash
python -m venv .venv && .venv/Scripts/activate && pip install -e .
```

## Настройка

```bash
dcurl init          # создать config.toml, .env, watchlist.txt из шаблонов
```

Заполните `.env`:
```
TG_BOT_TOKEN=...
TG_CHAT_ID=...
```

Заполните `watchlist.txt` — по одному коду/ссылке на строку:
```
discord.gg/example
discord.com/invite/another
```

Проверить уведомления:
```bash
dcurl test-notify
```

## Использование

```bash
dcurl check discord.gg/example              # разовая проверка
dcurl watch                                  # демон по watchlist.txt
dcurl watch discord.gg/one --interval 30    # конкретные коды
```

## Деплой на VPS (Ubuntu 24.04)

Вставьте `deploy/cloud-init.yml` в поле Cloud-init при создании сервера.

После загрузки:
```bash
nano /opt/dcurl/.env                  # TG_BOT_TOKEN, TG_CHAT_ID
nano /opt/dcurl/data/watchlist.txt
systemctl start dcurl
journalctl -u dcurl -f
```

Обновление:
```bash
bash /opt/dcurl/repo/deploy/update.sh
```

## Прокси (опционально)

В `config.toml`:
```toml
[proxy]
urls = "proxies.txt"      # файл, по одной на строку
# urls = ["http://user:pass@host:port"]
use_for_telegram = false  # true если Telegram недоступен напрямую
```

`proxies.txt` в git не попадает — добавьте его локально.
