# Сгенерировано flang (бэкенд Python, flang/self/emit-python.flang). Не редактировать руками.
# Модуль flang: «Trace brain».
# Файл: реализация: функции, конструкторы значений, вызов по имени.
# Правьте исходник на flang и печатайте заново: любая правка здесь потеряется.
"""
Модуль flang «Trace brain», напечатанный в Python.

Контракт вызова: функция возвращает значение либо возбуждает
flang_runtime.FlangError с кодом и текстом, дословно совпадающими с
интерпретатором flang. Все значения — flang_runtime.Value: числа там всегда
float (целых чисел в flang нет), признак отличается от числа тегом, а не
типом Python, и равенство скаляров — Object.is, а не ==.
"""

from ouroboros.brain._flang import flang_runtime as rt


def new_context():
    """Контекст вычисления с настройками этой программы.

    Индексация строк, предел глубины вызовов и лимит шагов — это настройки
    программы, а не рантайма: печать могла идти с нулевой базой индексации, а
    пределы вызывающий вправе поменять прямо в возвращённом контексте.
    """
    ctx = rt.new_ctx()
    ctx.index_base = 1
    ctx.max_depth = 10000
    ctx.max_steps = 1000000
    return ctx


def rec_entry(id2, started, name, args2, kwargs, has_cpu, cpu, thread):
    """Запись FTS «Entry»: «id», «started», «name», «args», «kwargs», «has cpu», «cpu», «thread».

    Запись flang тотальна: пропущенное поле — это «ничто», а не дырка.
    """
    return rt.record({
        "id": id2,
        "started": started,
        "name": name,
        "args": args2,
        "kwargs": kwargs,
        "has cpu": has_cpu,
        "cpu": cpu,
        "thread": thread,
    })


def rec_exit(id2, name, raised, raised_text, produced, produced_text, has_duration, duration):
    """Запись FTS «Exit»: «id», «name», «raised», «raised text», «produced», «produced text», «has duration», «duration».

    Запись flang тотальна: пропущенное поле — это «ничто», а не дырка.
    """
    return rt.record({
        "id": id2,
        "name": name,
        "raised": raised,
        "raised text": raised_text,
        "produced": produced,
        "produced text": produced_text,
        "has duration": has_duration,
        "duration": duration,
    })


def rec_call(index, started, call_id, name, args2, kwargs, outcome_kind, outcome, has_duration, duration, has_cpu, cpu, thread):
    """Запись FTS «Call»: «index», «started», «call id», «name», «args», «kwargs», «outcome kind», «outcome», «has duration», «duration», «has cpu», «cpu», «thread».

    Запись flang тотальна: пропущенное поле — это «ничто», а не дырка.
    """
    return rt.record({
        "index": index,
        "started": started,
        "call id": call_id,
        "name": name,
        "args": args2,
        "kwargs": kwargs,
        "outcome kind": outcome_kind,
        "outcome": outcome,
        "has duration": has_duration,
        "duration": duration,
        "has cpu": has_cpu,
        "cpu": cpu,
        "thread": thread,
    })


def rec_flight(name, call_id, started, has_cpu, cpu, thread):
    """Запись FTS «Flight»: «name», «call id», «started», «has cpu», «cpu», «thread».

    Запись flang тотальна: пропущенное поле — это «ничто», а не дырка.
    """
    return rt.record({
        "name": name,
        "call id": call_id,
        "started": started,
        "has cpu": has_cpu,
        "cpu": cpu,
        "thread": thread,
    })


def v_blank():
    """Вариант «Blank» суммы типов «Line kind».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("Blank", {})


def v_malformed():
    """Вариант «Malformed» суммы типов «Line kind».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("Malformed", {})


def v_candidate():
    """Вариант «Candidate» суммы типов «Line kind».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("Candidate", {})


def v_entered():
    """Вариант «Entered» суммы типов «Event kind».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("Entered", {})


def v_returned():
    """Вариант «Returned» суммы типов «Event kind».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("Returned", {})


def v_ignored():
    """Вариант «Ignored» суммы типов «Event kind».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("Ignored", {})


def v_raised(text):
    """Вариант «Raised» суммы типов «Outcome».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("Raised", {
        "text": text,
    })


def v_produced(text):
    """Вариант «Produced» суммы типов «Outcome».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("Produced", {
        "text": text,
    })


def v_silent():
    """Вариант «Silent» суммы типов «Outcome».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("Silent", {})


def v_oncpu(index):
    """Вариант «OnCpu» суммы типов «Cpu».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("OnCpu", {
        "index": index,
    })


def v_cpuunknown():
    """Вариант «CpuUnknown» суммы типов «Cpu».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("CpuUnknown", {})


def v_lasted(seconds):
    """Вариант «Lasted» суммы типов «Span».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("Lasted", {
        "seconds": seconds,
    })


def v_untimed():
    """Вариант «Untimed» суммы типов «Span».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("Untimed", {})


def v_argsfield():
    """Вариант «ArgsField» суммы типов «Field».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("ArgsField", {})


def v_kwargsfield():
    """Вариант «KwargsField» суммы типов «Field».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("KwargsField", {})


def v_producedfield():
    """Вариант «ProducedField» суммы типов «Field».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("ProducedField", {})


def v_raisedfield():
    """Вариант «RaisedField» суммы типов «Field».

    Дискриминант — имя варианта; проверяется через rt.variant_is(значение, «Имя»).
    Приставка v_ в имени — это роль: у функции flang с тем же именем идентификатор
    начинается с fn_, и одно объявление не съедает другое.
    """
    return rt.variant("RaisedField", {})


def fn_is_space(ctx, one):
    """Функция flang «Is space».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр one — «one»: строка.
    Результат — значение.
    """
    if rt.cond(ctx, rt.lte(ctx, rt.b_length(ctx, one), rt.number(0.0))):
        return rt.flag(False)
    else:
        # пусть «code»
        code = rt.b_char_code_proven(ctx, one)
        if rt.cond(ctx, rt.flag(rt.equal(code, rt.number(32.0)))):
            _t1 = rt.flag(True)
        else:
            if rt.cond(ctx, rt.gte(ctx, code, rt.number(9.0))):
                _t2 = rt.lte(ctx, code, rt.number(13.0))
            else:
                _t2 = rt.flag(False)
            _t1 = _t2
        if rt.cond(ctx, _t1):
            return rt.flag(True)
        else:
            if rt.cond(ctx, rt.gte(ctx, code, rt.number(28.0))):
                return rt.lte(ctx, code, rt.number(31.0))
            else:
                return rt.flag(False)


def fn_line_kind(ctx, line):
    """Функция flang «Line kind».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Хвостовой самовызов развёрнут в цикл: стек не растёт.

    Рекурсивная: считает глубину, на превышении — FLANG_RECURSION_LIMIT.

    Параметр line — «line»: строка.
    Результат — значение: «Line kind».
    """
    ctx.enter("Line kind")
    try:
        while True:
            if rt.cond(ctx, rt.b_starts_with(ctx, line, rt.text("{"))):
                return rt.variant("Candidate", {})
            else:
                if rt.chain_empty(line):
                    return rt.variant("Blank", {})
                elif rt.chain_cons(line):
                    # голова «head»
                    head = rt.chain_head(line)
                    # хвост «tail»
                    tail = rt.chain_tail(line)
                    if rt.cond(ctx, fn_is_space(ctx, head)):
                        line = tail
                        # виток цикла — тоже шаг вычисления: незавершающийся хвостовой
                        # самовызов обязан упереться в лимит, а не крутиться вечно
                        ctx.step("Line kind")
                        continue
                    else:
                        return rt.variant("Malformed", {})
                else:
                    raise rt.match_fail(ctx, line)
    finally:
        ctx.leave()


def fn_counts_as_malformed(ctx, line):
    """Функция flang «Counts as malformed».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр line — «line»: строка.
    Результат — значение.
    """
    _t3 = fn_line_kind(ctx, line)
    if rt.variant_is(_t3, "Malformed"):
        return rt.flag(True)
    elif rt.variant_is(_t3, "Blank"):
        return rt.flag(False)
    elif rt.variant_is(_t3, "Candidate"):
        return rt.flag(False)
    else:
        raise rt.match_fail(ctx, _t3)


def fn_event_kind(ctx, phase):
    """Функция flang «Event kind».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр phase — «phase»: строка.
    Результат — значение: «Event kind».
    """
    if rt.cond(ctx, rt.flag(rt.equal(phase, rt.text("in")))):
        return rt.variant("Entered", {})
    else:
        if rt.cond(ctx, rt.flag(rt.equal(phase, rt.text("out")))):
            return rt.variant("Returned", {})
        else:
            return rt.variant("Ignored", {})


def fn_outcome_of(ctx, raised, raised_text, produced, produced_text):
    """Функция flang «Outcome of».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр raised — «raised».
    Параметр raised_text — «raised text»: строка.
    Параметр produced — «produced».
    Параметр produced_text — «produced text»: строка.
    Результат — значение: «Outcome».
    """
    if rt.cond(ctx, raised):
        return rt.variant("Raised", {"text": raised_text})
    else:
        if rt.cond(ctx, produced):
            return rt.variant("Produced", {"text": produced_text})
        else:
            return rt.variant("Silent", {})


def fn_outcome_kind(ctx, outcome):
    """Функция flang «Outcome kind».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр outcome — «outcome»: «Outcome».
    Результат — значение: строка.
    """
    if rt.variant_is(outcome, "Raised"):
        # поле «text»
        _ = rt.variant_field(ctx, outcome, "text")
        return rt.text("raised")
    elif rt.variant_is(outcome, "Produced"):
        # поле «text»
        _ = rt.variant_field(ctx, outcome, "text")
        return rt.text("result")
    elif rt.variant_is(outcome, "Silent"):
        return rt.text("")
    else:
        raise rt.match_fail(ctx, outcome)


def fn_outcome_text(ctx, outcome):
    """Функция flang «Outcome text».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр outcome — «outcome»: «Outcome».
    Результат — значение: строка.
    """
    if rt.variant_is(outcome, "Raised"):
        # поле «text»
        one = rt.variant_field(ctx, outcome, "text")
        return one
    elif rt.variant_is(outcome, "Produced"):
        # поле «text»
        one2 = rt.variant_field(ctx, outcome, "text")
        return one2
    elif rt.variant_is(outcome, "Silent"):
        return rt.text("")
    else:
        raise rt.match_fail(ctx, outcome)


def fn_cpu_of(ctx, present, index):
    """Функция flang «Cpu of».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр present — «present».
    Параметр index — «index»: число.
    Результат — значение: «Cpu».
    """
    if rt.cond(ctx, present):
        _t4 = rt.gte(ctx, index, rt.number(0.0))
    else:
        _t4 = rt.flag(False)
    if rt.cond(ctx, _t4):
        return rt.variant("OnCpu", {"index": index})
    else:
        return rt.variant("CpuUnknown", {})


def fn_span_of(ctx, present, seconds):
    """Функция flang «Span of».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр present — «present».
    Параметр seconds — «seconds»: число.
    Результат — значение: «Span».
    """
    if rt.cond(ctx, present):
        return rt.variant("Lasted", {"seconds": seconds})
    else:
        return rt.variant("Untimed", {})


def fn_call_name(ctx, exit_name, entry_name):
    """Функция flang «Call name».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр exit_name — «exit name»: строка.
    Параметр entry_name — «entry name»: строка.
    Результат — значение: строка.
    """
    if rt.cond(ctx, rt.gt(ctx, rt.b_length(ctx, exit_name), rt.number(0.0))):
        return exit_name
    else:
        return entry_name


def fn_no_entry(ctx):
    """Функция flang «No entry».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Результат — значение: «Entry».
    """
    return rt.record({"id": rt.text(""), "started": rt.text(""), "name": rt.text(""), "args": rt.text(""), "kwargs": rt.text(""), "has cpu": rt.flag(False), "cpu": rt.number(0.0), "thread": rt.text("")})


def fn_completed_call(ctx, index, entry, exit):
    """Функция flang «Completed call».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр index — «index»: число.
    Параметр entry — «entry»: «Entry».
    Параметр exit — «exit»: «Exit».
    Результат — значение: «Call».
    """
    # пусть «outcome»
    outcome = fn_outcome_of(ctx, rt.field_get(ctx, exit, "raised"), rt.field_get(ctx, exit, "raised text"), rt.field_get(ctx, exit, "produced"), rt.field_get(ctx, exit, "produced text"))
    return rt.record({"index": index, "started": rt.field_get(ctx, entry, "started"), "call id": rt.field_get(ctx, exit, "id"), "name": fn_call_name(ctx, rt.field_get(ctx, exit, "name"), rt.field_get(ctx, entry, "name")), "args": rt.field_get(ctx, entry, "args"), "kwargs": rt.field_get(ctx, entry, "kwargs"), "outcome kind": fn_outcome_kind(ctx, outcome), "outcome": fn_outcome_text(ctx, outcome), "has duration": rt.field_get(ctx, exit, "has duration"), "duration": rt.field_get(ctx, exit, "duration"), "has cpu": rt.field_get(ctx, entry, "has cpu"), "cpu": rt.field_get(ctx, entry, "cpu"), "thread": rt.field_get(ctx, entry, "thread")})


def fn_same_call(ctx, entry, exit):
    """Функция flang «Same call».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр entry — «entry»: «Entry».
    Параметр exit — «exit»: «Exit».
    Результат — значение.
    """
    return rt.flag(rt.equal(rt.field_get(ctx, entry, "id"), rt.field_get(ctx, exit, "id")))


def fn_has_call_id(ctx, ids, wanted):
    """Функция flang «Has call id».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр ids — «ids»: список: строка.
    Параметр wanted — «wanted»: строка.
    Результат — значение.
    """
    _t5 = rt.require_list(ctx, ids, "свёртка")
    # «seen»
    seen = rt.flag(False)
    for one in _t5:
        if rt.cond(ctx, seen):
            _t6 = rt.flag(True)
        else:
            _t6 = rt.flag(rt.equal(one, wanted))
        seen = _t6
    return seen


def fn_is_in_flight(ctx, entry, completed_ids):
    """Функция flang «Is in flight».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр entry — «entry»: «Entry».
    Параметр completed_ids — «completed ids»: список: строка.
    Результат — значение.
    """
    if rt.cond(ctx, fn_has_call_id(ctx, completed_ids, rt.field_get(ctx, entry, "id"))):
        return rt.flag(False)
    else:
        return rt.flag(True)


def fn_flight_view(ctx, entry):
    """Функция flang «Flight view».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр entry — «entry»: «Entry».
    Результат — значение: «Flight».
    """
    return rt.record({"name": rt.field_get(ctx, entry, "name"), "call id": rt.field_get(ctx, entry, "id"), "started": rt.field_get(ctx, entry, "started"), "has cpu": rt.field_get(ctx, entry, "has cpu"), "cpu": rt.field_get(ctx, entry, "cpu"), "thread": rt.field_get(ctx, entry, "thread")})


def fn_in_flight(ctx, entries, completed_ids):
    """Функция flang «In flight».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр entries — «entries»: список: «Entry».
    Параметр completed_ids — «completed ids»: список: строка.
    Результат — значение: список: «Flight».
    """
    _t7 = rt.require_list(ctx, entries, "отфильтровать")
    _t8 = []
    for one in _t7:
        if rt.keep(ctx, fn_is_in_flight(ctx, one, completed_ids)):
            _t8.append(one)
    _t9 = rt.require_list(ctx, rt.list_of(_t8), "отобразить")
    _t10 = []
    for one2 in _t9:
        _t10.append(fn_flight_view(ctx, one2))
    return rt.list_of(_t10)


def fn_value_characters_limit(ctx):
    """Функция flang «Value characters limit».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Результат — значение: число.
    """
    return rt.number(200.0)


def fn_list_items_limit(ctx):
    """Функция flang «List items limit».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Результат — значение: число.
    """
    return rt.number(10.0)


def fn_record_bytes_limit(ctx):
    """Функция flang «Record bytes limit».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Результат — значение: число.
    """
    return rt.number(4096.0)


def fn_shrink_headroom(ctx):
    """Функция flang «Shrink headroom».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Результат — значение: число.
    """
    return rt.number(64.0)


def fn_shrink_budget(ctx):
    """Функция flang «Shrink budget».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Результат — значение: число.
    """
    return rt.sub(ctx, fn_record_bytes_limit(ctx), fn_shrink_headroom(ctx))


def fn_record_fits(ctx, bytes):
    """Функция flang «Record fits».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр bytes — «bytes»: число.
    Результат — значение.
    """
    return rt.lte(ctx, bytes, fn_record_bytes_limit(ctx))


def fn_within_shrink_budget(ctx, bytes):
    """Функция flang «Within shrink budget».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр bytes — «bytes»: число.
    Результат — значение.
    """
    return rt.lte(ctx, bytes, fn_shrink_budget(ctx))


def fn_floor_half(ctx, value):
    """Функция flang «Floor half».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр value — «value»: число.
    Результат — значение: число.
    """
    # пусть «half»
    half = rt.div(ctx, value, rt.number(2.0))
    return rt.sub(ctx, half, rt.mod(ctx, half, rt.number(1.0)))


def fn_head_chars(ctx, text, count):
    """Функция flang «Head chars».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр text — «text»: строка.
    Параметр count — «count»: число.
    Результат — значение: строка.
    """
    if rt.cond(ctx, rt.lte(ctx, count, rt.number(0.0))):
        return rt.text("")
    else:
        if rt.cond(ctx, rt.gte(ctx, count, rt.b_length(ctx, text))):
            return text
        else:
            return rt.b_substring(ctx, text, rt.number(1.0), count)


def fn_tail_chars(ctx, text, count):
    """Функция flang «Tail chars».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр text — «text»: строка.
    Параметр count — «count»: число.
    Результат — значение: строка.
    """
    if rt.cond(ctx, rt.lte(ctx, count, rt.number(0.0))):
        return rt.text("")
    else:
        if rt.cond(ctx, rt.gte(ctx, count, rt.b_length(ctx, text))):
            return text
        else:
            return rt.b_substring(ctx, text, rt.add(ctx, rt.sub(ctx, rt.b_length(ctx, text), count), rt.number(1.0)), rt.b_length(ctx, text))


def fn_shorten_text(ctx, text, limit):
    """Функция flang «Shorten text».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр text — «text»: строка.
    Параметр limit — «limit»: число.
    Результат — значение: строка.
    """
    if rt.cond(ctx, rt.lte(ctx, rt.b_length(ctx, text), limit)):
        return text
    else:
        if rt.cond(ctx, rt.lte(ctx, limit, rt.number(3.0))):
            _t11 = rt.number(0.0)
        else:
            _t11 = rt.sub(ctx, limit, rt.number(3.0))
        # пусть «room»
        room = _t11
        # пусть «front»
        front = fn_floor_half(ctx, room)
        # пусть «back»
        back = rt.sub(ctx, room, front)
        return rt.b_join(ctx, rt.list_of([fn_head_chars(ctx, text, front), rt.text("..."), fn_tail_chars(ctx, text, back)]), rt.text(""))


def fn_first_items(ctx, items, count):
    """Функция flang «First items».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Рекурсивная: считает глубину, на превышении — FLANG_RECURSION_LIMIT.

    Параметр items — «items»: список: «A».
    Параметр count — «count»: число.
    Результат — значение: список: «A».
    """
    ctx.enter("First items")
    try:
        if rt.cond(ctx, rt.lte(ctx, count, rt.number(0.0))):
            return rt.list_of([])
        else:
            if rt.chain_empty(items):
                return rt.list_of([])
            elif rt.chain_cons(items):
                # голова «head»
                head = rt.chain_head(items)
                # хвост «tail»
                tail = rt.chain_tail(items)
                return rt.b_prepend(ctx, head, fn_first_items(ctx, tail, rt.sub(ctx, count, rt.number(1.0))))
            else:
                raise rt.match_fail(ctx, items)
    finally:
        ctx.leave()


def fn_shorten_items(ctx, pieces, limit):
    """Функция flang «Shorten items».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр pieces — «pieces»: список: строка.
    Параметр limit — «limit»: число.
    Результат — значение: список: строка.
    """
    if rt.cond(ctx, rt.lte(ctx, rt.b_length(ctx, pieces), limit)):
        return pieces
    else:
        return rt.b_append(ctx, rt.text("..."), fn_first_items(ctx, pieces, limit))


def fn_longest_field(ctx, args2, kwargs, produced, raised):
    """Функция flang «Longest field».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр args2 — «args»: строка.
    Параметр kwargs — «kwargs»: строка.
    Параметр produced — «produced»: строка.
    Параметр raised — «raised»: строка.
    Результат — значение: «Field».
    """
    # пусть «la»
    la = rt.b_length(ctx, args2)
    # пусть «lk»
    lk = rt.b_length(ctx, kwargs)
    # пусть «lr»
    lr = rt.b_length(ctx, produced)
    # пусть «lx»
    lx = rt.b_length(ctx, raised)
    if rt.cond(ctx, rt.gte(ctx, la, lk)):
        _t12 = rt.gte(ctx, la, lr)
    else:
        _t12 = rt.flag(False)
    if rt.cond(ctx, _t12):
        _t13 = rt.gte(ctx, la, lx)
    else:
        _t13 = rt.flag(False)
    if rt.cond(ctx, _t13):
        return rt.variant("ArgsField", {})
    else:
        if rt.cond(ctx, rt.gte(ctx, lk, lr)):
            _t14 = rt.gte(ctx, lk, lx)
        else:
            _t14 = rt.flag(False)
        if rt.cond(ctx, _t14):
            return rt.variant("KwargsField", {})
        else:
            if rt.cond(ctx, rt.gte(ctx, lr, lx)):
                return rt.variant("ProducedField", {})
            else:
                return rt.variant("RaisedField", {})


def fn_anything_to_shrink(ctx, args2, kwargs, produced, raised):
    """Функция flang «Anything to shrink».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр args2 — «args»: строка.
    Параметр kwargs — «kwargs»: строка.
    Параметр produced — «produced»: строка.
    Параметр raised — «raised»: строка.
    Результат — значение.
    """
    if rt.cond(ctx, rt.gt(ctx, rt.b_length(ctx, args2), rt.number(0.0))):
        _t15 = rt.flag(True)
    else:
        _t15 = rt.gt(ctx, rt.b_length(ctx, kwargs), rt.number(0.0))
    if rt.cond(ctx, _t15):
        _t16 = rt.flag(True)
    else:
        _t16 = rt.gt(ctx, rt.b_length(ctx, produced), rt.number(0.0))
    if rt.cond(ctx, _t16):
        return rt.flag(True)
    else:
        return rt.gt(ctx, rt.b_length(ctx, raised), rt.number(0.0))


def fn_halved(ctx, text):
    """Функция flang «Halved».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр text — «text»: строка.
    Результат — значение: строка.
    """
    return fn_head_chars(ctx, text, fn_floor_half(ctx, rt.b_length(ctx, text)))


def fn_cpu_known(ctx, cpu):
    """Функция flang «Cpu known».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр cpu — «cpu»: «Cpu».
    Результат — значение.
    """
    if rt.variant_is(cpu, "OnCpu"):
        # поле «index»
        _ = rt.variant_field(ctx, cpu, "index")
        return rt.flag(True)
    elif rt.variant_is(cpu, "CpuUnknown"):
        return rt.flag(False)
    else:
        raise rt.match_fail(ctx, cpu)


def fn_cpu_index(ctx, cpu):
    """Функция flang «Cpu index».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр cpu — «cpu»: «Cpu».
    Результат — значение: число.
    """
    if rt.variant_is(cpu, "OnCpu"):
        # поле «index»
        one = rt.variant_field(ctx, cpu, "index")
        return one
    elif rt.variant_is(cpu, "CpuUnknown"):
        return rt.number(0.0)
    else:
        raise rt.match_fail(ctx, cpu)


def fn_span_timed(ctx, span):
    """Функция flang «Span timed».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр span — «span»: «Span».
    Результат — значение.
    """
    if rt.variant_is(span, "Lasted"):
        # поле «seconds»
        _ = rt.variant_field(ctx, span, "seconds")
        return rt.flag(True)
    elif rt.variant_is(span, "Untimed"):
        return rt.flag(False)
    else:
        raise rt.match_fail(ctx, span)


def fn_span_seconds(ctx, span):
    """Функция flang «Span seconds».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр span — «span»: «Span».
    Результат — значение: число.
    """
    if rt.variant_is(span, "Lasted"):
        # поле «seconds»
        one = rt.variant_field(ctx, span, "seconds")
        return one
    elif rt.variant_is(span, "Untimed"):
        return rt.number(0.0)
    else:
        raise rt.match_fail(ctx, span)


def fn_entry_from(ctx, id2, started, name, args2, kwargs, cpu_present, cpu_index, thread):
    """Функция flang «Entry from».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр id2 — «id»: строка.
    Параметр started — «started»: строка.
    Параметр name — «name»: строка.
    Параметр args2 — «args»: строка.
    Параметр kwargs — «kwargs»: строка.
    Параметр cpu_present — «cpu present».
    Параметр cpu_index — «cpu index»: число.
    Параметр thread — «thread»: строка.
    Результат — значение: «Entry».
    """
    # пусть «cpu»
    cpu = fn_cpu_of(ctx, cpu_present, cpu_index)
    return rt.record({"id": id2, "started": started, "name": name, "args": args2, "kwargs": kwargs, "has cpu": fn_cpu_known(ctx, cpu), "cpu": fn_cpu_index(ctx, cpu), "thread": thread})


def fn_exit_from(ctx, id2, name, raised, raised_text, produced, produced_text, duration_present, duration):
    """Функция flang «Exit from».

    Тотальная: завершение доказано анализом завершаемости (totality.mjs).

    Параметр id2 — «id»: строка.
    Параметр name — «name»: строка.
    Параметр raised — «raised».
    Параметр raised_text — «raised text»: строка.
    Параметр produced — «produced».
    Параметр produced_text — «produced text»: строка.
    Параметр duration_present — «duration present».
    Параметр duration — «duration»: число.
    Результат — значение: «Exit».
    """
    # пусть «span»
    span = fn_span_of(ctx, duration_present, duration)
    return rt.record({"id": id2, "name": name, "raised": raised, "raised text": raised_text, "produced": produced, "produced text": produced_text, "has duration": fn_span_timed(ctx, span), "duration": fn_span_seconds(ctx, span)})


def call(ctx, name, args):
    """Вызов функции по её исходному имени flang.

    Нужен прогонщику и всякому, кто связывает программу с внешним миром
    динамически (скрипт, тест, служба). Коды и тексты — те же, что у
    интерпретатора: «не найдена функция …» и «функция … принимает N аргум.».
    """
    if name == "Is space":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Is space» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_is_space(ctx, args[0])
    if name == "Line kind":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Line kind» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_line_kind(ctx, args[0])
    if name == "Counts as malformed":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Counts as malformed» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_counts_as_malformed(ctx, args[0])
    if name == "Event kind":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Event kind» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_event_kind(ctx, args[0])
    if name == "Outcome of":
        if len(args) != 4:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Outcome of» принимает 4 аргум., получено "
                + str(len(args)),
            )
        return fn_outcome_of(ctx, args[0], args[1], args[2], args[3])
    if name == "Outcome kind":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Outcome kind» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_outcome_kind(ctx, args[0])
    if name == "Outcome text":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Outcome text» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_outcome_text(ctx, args[0])
    if name == "Cpu of":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Cpu of» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_cpu_of(ctx, args[0], args[1])
    if name == "Span of":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Span of» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_span_of(ctx, args[0], args[1])
    if name == "Call name":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Call name» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_call_name(ctx, args[0], args[1])
    if name == "No entry":
        if len(args) != 0:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «No entry» принимает 0 аргум., получено "
                + str(len(args)),
            )
        return fn_no_entry(ctx)
    if name == "Completed call":
        if len(args) != 3:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Completed call» принимает 3 аргум., получено "
                + str(len(args)),
            )
        return fn_completed_call(ctx, args[0], args[1], args[2])
    if name == "Same call":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Same call» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_same_call(ctx, args[0], args[1])
    if name == "Has call id":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Has call id» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_has_call_id(ctx, args[0], args[1])
    if name == "Is in flight":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Is in flight» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_is_in_flight(ctx, args[0], args[1])
    if name == "Flight view":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Flight view» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_flight_view(ctx, args[0])
    if name == "In flight":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «In flight» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_in_flight(ctx, args[0], args[1])
    if name == "Value characters limit":
        if len(args) != 0:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Value characters limit» принимает 0 аргум., получено "
                + str(len(args)),
            )
        return fn_value_characters_limit(ctx)
    if name == "List items limit":
        if len(args) != 0:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «List items limit» принимает 0 аргум., получено "
                + str(len(args)),
            )
        return fn_list_items_limit(ctx)
    if name == "Record bytes limit":
        if len(args) != 0:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Record bytes limit» принимает 0 аргум., получено "
                + str(len(args)),
            )
        return fn_record_bytes_limit(ctx)
    if name == "Shrink headroom":
        if len(args) != 0:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Shrink headroom» принимает 0 аргум., получено "
                + str(len(args)),
            )
        return fn_shrink_headroom(ctx)
    if name == "Shrink budget":
        if len(args) != 0:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Shrink budget» принимает 0 аргум., получено "
                + str(len(args)),
            )
        return fn_shrink_budget(ctx)
    if name == "Record fits":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Record fits» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_record_fits(ctx, args[0])
    if name == "Within shrink budget":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Within shrink budget» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_within_shrink_budget(ctx, args[0])
    if name == "Floor half":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Floor half» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_floor_half(ctx, args[0])
    if name == "Head chars":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Head chars» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_head_chars(ctx, args[0], args[1])
    if name == "Tail chars":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Tail chars» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_tail_chars(ctx, args[0], args[1])
    if name == "Shorten text":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Shorten text» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_shorten_text(ctx, args[0], args[1])
    if name == "First items":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «First items» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_first_items(ctx, args[0], args[1])
    if name == "Shorten items":
        if len(args) != 2:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Shorten items» принимает 2 аргум., получено "
                + str(len(args)),
            )
        return fn_shorten_items(ctx, args[0], args[1])
    if name == "Longest field":
        if len(args) != 4:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Longest field» принимает 4 аргум., получено "
                + str(len(args)),
            )
        return fn_longest_field(ctx, args[0], args[1], args[2], args[3])
    if name == "Anything to shrink":
        if len(args) != 4:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Anything to shrink» принимает 4 аргум., получено "
                + str(len(args)),
            )
        return fn_anything_to_shrink(ctx, args[0], args[1], args[2], args[3])
    if name == "Halved":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Halved» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_halved(ctx, args[0])
    if name == "Cpu known":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Cpu known» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_cpu_known(ctx, args[0])
    if name == "Cpu index":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Cpu index» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_cpu_index(ctx, args[0])
    if name == "Span timed":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Span timed» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_span_timed(ctx, args[0])
    if name == "Span seconds":
        if len(args) != 1:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Span seconds» принимает 1 аргум., получено "
                + str(len(args)),
            )
        return fn_span_seconds(ctx, args[0])
    if name == "Entry from":
        if len(args) != 8:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Entry from» принимает 8 аргум., получено "
                + str(len(args)),
            )
        return fn_entry_from(ctx, args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7])
    if name == "Exit from":
        if len(args) != 8:
            raise rt.fail(
                rt.CODE_TYPE,
                "функция «Exit from» принимает 8 аргум., получено "
                + str(len(args)),
            )
        return fn_exit_from(ctx, args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7])
    raise rt.fail(rt.CODE_UNKNOWN_NAME, "не найдена функция «" + name + "»")


# Граница входа: объявленные типы параметров данными.
#
# Прогонщик сверяет по ним значения, пришедшие снаружи, ДО вызова
# (rt.check_entry). Виды rt.TYPE_UNKNOWN (значение-функция, параметр
# полиморфизма, применение типа с аргументами) не сверяются — ровно как
# молчит о них проверка значений свидетеля.
_ENTRY = rt.EntryTable(
    [
    ],
    [
    ],
    [
    ],
    [
    ],
)


def entry():
    """Объявленные типы параметров: по ним сверяется вход извне."""
    return _ENTRY
