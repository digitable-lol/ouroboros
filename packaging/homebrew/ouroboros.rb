# Формула Homebrew для уробороса.
#
# ЧТО ЭТО. Исходник формулы. Правится здесь; в хранилище формул
# (digitable-lol/homebrew-tap, каталог Formula/) он выкладывается копией, и
# оттуда работает короткая строка
#
#     brew install digitable-lol/tap/ouroboros
#
# Пока хранилища формул нет, ставится по прямому адресу этого файла:
#
#     brew install --formula \
#       https://raw.githubusercontent.com/digitable-lol/ouroboros/main/packaging/homebrew/ouroboros.rb
#
# ЧТО ПРОСТАВЛЕНО. url и sha256 — настоящие, от выпуска v0.2.0. Отпечаток
# посчитан с того самого архива, который отдаёт GitHub:
#
#     curl -sL https://github.com/digitable-lol/ouroboros/archive/refs/tags/v0.2.0.tar.gz | sha256sum
#
# Установка проверена целиком: brew install из этого файла и brew test.
#
class Ouroboros < Formula
  include Language::Python::Virtualenv

  desc "Records how code actually ran: calls, arguments, results, exceptions"
  homepage "https://github.com/digitable-lol/ouroboros"
  url "https://github.com/digitable-lol/ouroboros/archive/refs/tags/v0.2.0.tar.gz"
  sha256 "75e6e2100232b9c2cc998e3c306f71d53646b62ef782dbad8af18fc1730186d7"
  license "BSD-2-Clause"

  # Пакет требует Python 3.12 или новее (pyproject.toml, requires-python).
  depends_on "python@3.12"

  # Что НЕ вынесено в зависимости и почему:
  #
  #   libclang       — приходит зависимостью самого пакета, отдельно не нужен;
  #   @babel/parser  — лежит внутри пакета, npm install не нужен;
  #   llvm, node,    — нужны, только чтобы СОБРАТЬ и ЗАПУСТИТЬ обмазанный код на
  #   elixir           C/C++, JavaScript и Elixir. Тянуть их каждому, кто ставит
  #                    инструмент ради Python, неправильно. См. caveats ниже.
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
      Компилятор C и C++ берётся системный.

      Страницы: https://digitable-lol.github.io/ouroboros/
    EOS
  end

  test do
    # 1. Обе команды встали на место и запускаются.
    assert_match "python", shell_output("#{bin}/ouroboros languages")

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
