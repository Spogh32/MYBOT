import sys
import os

def main():
    os.chdir("scripts/netflix-bot")
    sys.path.insert(0, os.getcwd())
    from dashboard import start_dashboard
    start_dashboard(port=5000)
    import bot
    bot.main()

if __name__ == "__main__":
    main()
