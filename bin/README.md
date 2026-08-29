# `bin/` — точка входа плагина asdf

`asdf` ждёт `bin/list-all`, `bin/download` и `bin/install` в **корне** хранилища
плагина. Поэтому три файла лежат здесь; каждый — одна строка, передающая работу
настоящему плагину в [`../packaging/asdf/bin/`](../packaging/asdf/bin/).

Так плагин ставится прямо из этого хранилища, без отдельного:

```sh
asdf plugin add ouroboros https://github.com/digitable-lol/ouroboros.git
asdf install ouroboros latest
```

Правьте `packaging/asdf/bin/`, а не эти файлы. Командам самого инструмента этот
каталог отношения не имеет — они называются `ouroboros` и `ouroboros-mcp` и
ставятся в окружение, которое собирает `packaging/asdf/bin/install`.
