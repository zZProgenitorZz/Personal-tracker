"""Stop Progen netjes (voor de snelkoppeling "Stop Progen"; het icoon in het
systeemvak heeft dezelfde knop). De server rondt lopende verzoeken af en sluit
de database; dat is netter dan het proces af te schieten.
"""
from app.desktop import request_stop, show_message


def main() -> None:
    problem = request_stop()
    if problem:
        show_message(problem, error=False)


if __name__ == "__main__":
    main()
