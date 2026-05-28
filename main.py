async def main():
    init_db()

    scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")  # ← shunday bo'lishi kerak
    scheduler.add_job(send_daily_report, "cron", hour=20, minute=0)
    scheduler.start()
