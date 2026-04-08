FROM python:3.10

WORKDIR /app

COPY . .

RUN pip install graphql-core

RUN chmod +x detect_changes.sh

CMD ["bash", "detect_changes.sh"]