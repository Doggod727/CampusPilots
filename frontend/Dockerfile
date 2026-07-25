# build：安装依赖并产出 dist
FROM node:20-alpine AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.4.0 --activate
COPY package.json pnpm-lock.yaml .npmrc ./
# 仓库内 .npmrc 指向 Windows 本机 store，镜像内改写并提高弱网重试；store 用 BuildKit 缓存卷跨构建复用
RUN --mount=type=cache,target=/pnpm-store \
    sed -i 's#^store-dir=.*#store-dir=/pnpm-store#' .npmrc \
    && printf 'fetch-retries=5\nfetch-retry-maxtimeout=120000\nnetwork-concurrency=8\n' >> .npmrc \
    && pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

# runtime：Nginx 托管静态产物并反代后端
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
