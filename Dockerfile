FROM node:20-alpine

WORKDIR /app

RUN apk add --no-cache python3

COPY package.json ./

RUN npm install --no-audit --no-fund

COPY . .

EXPOSE 4000

CMD ["npm", "start"]

