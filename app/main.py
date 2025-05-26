import sys
import asyncio
import host # Assuming host.py is in the same directory
import user # Assuming user.py is in the same directory

async def main():
    mode = None

    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == "host":
            mode = "host"
        elif arg == "participant":
            mode = "participant"
        else:
            print("Invalid argument. Use 'host' or 'participant'.")
            sys.exit(1)
    else:
        print("Select mode:")
        print("0: Host")
        print("1: Participant")
        while mode is None:
            choice = input("Enter 0 or 1: ")
            if choice == "0":
                mode = "host"
            elif choice == "1":
                mode = "participant"
            else:
                print("Invalid choice. Please enter 0 or 1.")

    if mode == "host":
        print("Starting in Host mode...")
        await host.connect()
    elif mode == "participant":
        print("Starting in Participant mode...")
        await user.connect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopping...")
