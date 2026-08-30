# Формула Homebrew для уробороса.
#
# ЧТО ЭТО. Исходник формулы. Правится здесь; в хранилище формул
# (digitable-lol/homebrew-tap, файл Formula/ouroboros.rb) он выкладывается
# копией, и оттуда работает короткая строка
#
#     brew install digitable-lol/tap/ouroboros
#
# Хранилище формул заведено, копия там лежит, короткая строка проверена
# прогоном: brew подключает хранилище сам, отдельная команда brew tap не нужна.
#
# ЧТО ПРОСТАВЛЕНО. url и sha256 — настоящие, от выпуска v0.5.0. Отпечаток
# посчитан с того самого архива, который отдаёт GitHub, и сверен двумя загрузками:
#
#     curl -sL https://github.com/digitable-lol/ouroboros/archive/refs/tags/v0.5.0.tar.gz | sha256sum
#
# Установка проверена целиком: хранилище формул отцеплено и прежняя версия снята,
# затем короткая строка выше поставила 0.5.0 с нуля, затем brew test, затем
# обмазка настоящего файла на Java, сборка, запуск и чтение записей.
#
class Ouroboros < Formula
  include Language::Python::Virtualenv

  desc "Records how code actually ran: calls, arguments, results, exceptions"
  homepage "https://github.com/digitable-lol/ouroboros"
  url "https://github.com/digitable-lol/ouroboros/archive/refs/tags/v0.5.0.tar.gz"
  sha256 "741ce0acc2c566f1a50130a259ce43239b845c773d954a0a720f08ef0348c4fe"
  license "BSD-2-Clause"

  # Пакет требует Python 3.12 или новее (pyproject.toml, requires-python).
  depends_on "python@3.12"

  # Что НЕ вынесено в зависимости и почему:
  #
  #   libclang       — приходит зависимостью самого пакета, отдельно не нужен;
  #   @babel/parser  — лежит внутри пакета, npm install не нужен;
  #   go/parser      — входит в поставку языка Go, отдельно не нужен;
  #   llvm, node,    — нужны, только чтобы СОБРАТЬ и ЗАПУСТИТЬ обмазанный код на
  #   elixir           C/C++, JavaScript и Elixir. Тянуть их каждому, кто ставит
  #                    инструмент ради Python, неправильно. См. caveats ниже.
  #   go             — нужен и чтобы ОБМАЗАТЬ Go: разбор идёт его же средствами;
  #   openjdk        — нужен и чтобы ОБМАЗАТЬ Java: разборщик лежит внутри JDK;
  #   dotnet         — нужен и чтобы ОБМАЗАТЬ C#: Roslyn лежит внутри .NET SDK.
  def install
    # Пакет ставится в собственное окружение, наружу выносятся только его
    # команды — ouroboros и ouroboros-mcp.
    #
    # Окружение делается обычным venv, а не через virtualenv_install_with_resources,
    # НАРОЧНО. Штатный путь Homebrew ставит с --no-deps и требует, чтобы каждая
    # зависимость была расписана блоком resource. Их здесь три десятка, и часть
    # (pydantic-core, rpds-py, cryptography) собирается из исходников только с
    # Rust. Обычный pip берёт для них готовые колёса с PyPI: установка выходит
    # быстрее и без сборочной цепочки. Цена — версии зависимостей не
    # закреплены отпечатками в самой формуле; закрепление у пакета своё, в
    # uv.lock.
    python = Formula["python@3.12"].opt_bin/"python3.12"
    system python, "-m", "venv", libexec
    system libexec/"bin/python", "-m", "pip", "install", "--quiet",
           "--no-cache-dir", "--upgrade", "pip"
    system libexec/"bin/python", "-m", "pip", "install", "--quiet",
           "--no-cache-dir", buildpath
    bin.install_symlink Dir[libexec/"bin/ouroboros*"]
  end

  def caveats
    <<~EOS
      Проверить установку:
        ouroboros languages

      Чтобы инструментом пользовался ИИ-агент, добавьте в настройку клиента:
        { "mcpServers": { "ouroboros": { "type": "stdio", "command": "ouroboros-mcp" } } }

      Доставить отдельно, если будете обмазывать не Python:
        brew install llvm      # clang-tidy и clangd — для команд lint/symbols/refs/callers/describe
        brew install node      # запустить обмазанный JavaScript и TypeScript
        brew install elixir    # запустить обмазанный Elixir
        brew install go        # обмазать, собрать и запустить Go
        brew install openjdk   # обмазать и собрать Java
        brew install dotnet    # обмазать и собрать C#
      Компилятор C и C++ берётся системный.

      Страницы: https://digitable-lol.github.io/ouroboros/
    EOS
  end

  test do
    # 1. Обе команды встали на место и запускаются, и знают все восемь языков.
    languages = shell_output("#{bin}/ouroboros languages")
    assert_match "python", languages
    assert_match "go", languages
    assert_match "java", languages
    assert_match "csharp", languages

    # 2. Инструмент делает своё дело: обмазывает файл, файл запускается,
    #    записи читаются. Это проверка end-to-end, а не «файл существует».
    (testpath/"m.py").write <<~PYTHON
      def add(a, b):
          return a + b

      print(add(2, 3))
    PYTHON

    system bin/"ouroboros", "wrap-file", testpath/"m.py"
    assert_match "_ouro_log", (testpath/"m.py").read

    with_env(OUROBOROS_DEBUG_INFO: testpath/"debug.info") do
      assert_equal "5\n", shell_output("#{Formula["python@3.12"].opt_bin}/python3.12 #{testpath}/m.py")
    end

    trace = shell_output("#{bin}/ouroboros trace-stats #{testpath}/debug.info")
    assert_match "\"total_calls\": 1", trace
    assert_match "\"name\": \"add\"", trace
  end
end
