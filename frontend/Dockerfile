# Stage 1: Build the frontend
FROM node:22-alpine AS build

WORKDIR /app

# Copy package files and install dependencies (if needed)
COPY frontend/package*.json ./
RUN npm install

# Copy shared resources first
COPY shared_resources/word_guess/valid-guesses.txt ./public/

# Copy source code and build
COPY frontend/ .
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:stable-alpine

# Copy built files from previous stage to nginx html folder
COPY --from=build /app/dist /usr/share/nginx/html

# Copy custom nginx config
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

# Start nginx server
CMD ["nginx", "-g", "daemon off;"]