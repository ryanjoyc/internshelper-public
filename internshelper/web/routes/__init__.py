from internshelper.web.routes import board, companies, health, postings, sources

all_routers = [
    board.router,
    postings.router,
    health.router,
    sources.router,
    companies.router,
]
