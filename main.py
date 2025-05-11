import asyncio, os
from email_confirm import MailWorker, MailProducer

# задать пароль заранее или через окружение
os.environ["SMTP_PASS"] = "APPLESS_PASSWORD"

async def main():
    # запустить одного воркера
    asyncio.create_task(MailWorker().run())
    producer = MailProducer()

    # шаг 1: запросить отправку
    ok = await producer.email_confirm("azzamkulovshokhruz@gmail.com")
    print("запрос отправки:", ok)  

    # каждые 5 с проверяем статус и выводим
    for _ in range(7):
        st = await producer.is_confirm("azzamkulovshokhruz@gmail.com")
        print("статус:", st)
        await asyncio.sleep(5)

    # шаг 2: когда пользователь получил письмо и перешёл по ссылке,
    # ваш HTTP-эндпоинт вызывает:
    #   await producer.mark_confirmed(token)
    # здесь имитируем вызов:
    fake_token = input("введите токен из ссылки: ")
    confirmed = await producer.mark_confirmed(fake_token)
    print("подтверждение выполнено:", confirmed)

    # проверим ещё раз — запись должна исчезнуть
    st2 = await producer.is_confirm("azzamkulovshokhruz@gmail.com")
    print("после подтверждения:", st2)

asyncio.run(main())