# Selectel Floating IP Hunter

Асинхронный Telegram-бот на Python 3.11+, aiogram 3, aiohttp и SQLAlchemy/SQLite. Бот хранит credentials только в зашифрованном виде Fernet, запускает независимый watcher для каждой пары `account_id + subnet_id`, восстанавливает RUNNING-задачи после рестарта и не удаляет найденные Floating IP.

## Локальный запуск

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
python -m app.main
```

Заполните `BOT_TOKEN`, `ADMIN_IDS` и `ENCRYPTION_KEY`. Доступ ограничивается списком Telegram ID; остальные пользователи получают отказ.

`NOTIFICATION_CHAT_ID` — ID Telegram-супергруппы с включёнными Topics. Бот должен быть администратором с правом управления темами. При добавлении аккаунта бот создаёт отдельный topic; туда отправляются сообщения о добавлении, ошибках API и найденных IP.

При добавлении аккаунта можно указать отдельный proxy (`http://user:password@host:port`), минимальную и максимальную задержку. Proxy хранится зашифрованным вместе с паролем. Для одного аккаунта разрешена только одна активная целевая подсеть.

## Ubuntu 22.04/24.04 и systemd

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
sudo mkdir -p /opt/selectel-ip-hunter
sudo cp -r . /opt/selectel-ip-hunter
cd /opt/selectel-ip-hunter && python3 -m venv venv
source venv/bin/activate && pip install -r requirements.txt
cp .env.example .env && nano .env
sudo cp selectel-ip-hunter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now selectel-ip-hunter
sudo journalctl -u selectel-ip-hunter -f
```

API credentials вводятся через Telegram wizard. Пароль не попадает в логи или сообщения.

На один круг аккаунта используется один клиент и одна HTTP-сессия. Соединения
повторно используются между запросами к одному API, в том числе через прокси.
Перед кругом старая сессия закрывается, в конце круга сессия закрывается также
при ошибке или отмене. После сетевого сбоя соединение может быть пересоздано.
Разные региональные API используют отдельные соединения внутри одной сессии.
Таймаут API по умолчанию остаётся 5 секунд.

Планировщик запускает круги разных аккаунтов независимо по их расписанию:
ожидание ответа или повтор запроса одного аккаунта не задерживает запуск других.
Внутри аккаунта подсети проверяются последовательно с заданной задержкой
(например, 0,5 секунды). Одновременно выполняется не больше одного круга
каждого аккаунта; следующий круг начинается после его завершения и cooldown.
