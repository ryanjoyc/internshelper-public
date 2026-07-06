from internshelper.web.routes import board, health, postings, review, sources

all_routers = [
    review.router,
    board.router,
    postings.router,
    health.router,
    sources.router,
]
