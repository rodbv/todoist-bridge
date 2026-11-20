"""Saved Info text that teaches Gemini to save dated commands as Google Tasks.

The README shows the same text; a test keeps the two copies equal.
"""

SAVED_INFO_URL = "https://gemini.google.com/saved-info"

SAVED_INFO_PT = (
    "Quando eu disser uma frase no imperativo com uma data ou hora, absoluta ou "
    "relativa, crie uma tarefa no Google Tasks, na lista Minhas tarefas, sem pedir "
    'confirmação. Exemplos: "ligar pro dentista amanhã às 9h30", "pagar a conta '
    'sexta", "regar as plantas em 10 minutos", "tirar o lixo toda quinta às 22h". Use '
    "a frase inteira como título da tarefa, com a data, a hora e a repetição "
    "exatamente como eu falei. Nunca remova do título as expressões de data, hora ou "
    "repetição. Nunca crie tarefas recorrentes no Google Tasks: escreva a repetição "
    'no título, por exemplo "tomar remédio todo dia às 22h15", e o Todoist cuida da '
    "repetição. Não use o Google Keep nem lembretes do Assistente."
)

SAVED_INFO_EN = (
    "When I say an imperative sentence with a date or time, absolute or relative, "
    "create a task in Google Tasks, in the My Tasks list, without asking for "
    'confirmation. Examples: "call the dentist tomorrow at 9:30am", "pay the bill on '
    'Friday", "water the plants in 10 minutes", "take out the trash every Thursday at '
    '10pm". Use the whole sentence as the task title, with the date, time and '
    "recurrence exactly as I said them. Never remove date, time or recurrence "
    "expressions from the title. Never create recurring tasks in Google Tasks: write "
    'the recurrence in the title, for example "take my medicine every day at '
    '10:15pm", and Todoist handles the repetition. Do not use Google Keep or '
    "Assistant reminders."
)


def saved_info_for(language: str) -> str:
    return SAVED_INFO_PT if language.lower().startswith("pt") else SAVED_INFO_EN
