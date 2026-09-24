# QuickFood - Backend

This is the backend API for the QuickFood application, a food delivery platform that connects customers with restaurants.

API Documentation: https://documenter.getpostman.com/view/21646395/2sAYkGLekx

Base API is available at https://quickfood-backend-hoi3.onrender.com/
Frontend repo: https://github.com/junaaid96/quickfood_frontend 
Frontend live: https://quickfood-frontend.vercel.app/

## Project Overview

- Restaurant owners manage restaurants and menus, run a live kitchen board and read analytics
- Customers discover restaurants, build a cart, check out with transparent pricing and track orders live
- Authentication and authorization for different user roles

## Highlights

- **Discovery**: search across restaurants *and dishes*, filters (cuisine, dietary, open now, free delivery, price, rating) and sorting (recommended, rating, fastest, fee, popular)
- **Budget Bites meal planner**: give a budget and party size, get complete meals (mains, sides, drinks, dessert) that fit the whole bill, fees included
- **Transparent pricing**: one server-side quote engine drives both checkout previews and real orders
- **Delivery options**: Priority, Standard, or eco *Wait & Save* (batched, cheaper, lower emissions)
- **Promo codes, tips, scheduled orders and loyalty points** (earn 1 point per $1, redeem 100 points = $1, tiers Bronze to Platinum)
- **Order lifecycle**: forward-only statuses with a timestamped timeline, customer self-cancel while pending, one-tap reorder
- **Order chat** between customer and restaurant
- **Verified reviews** (only delivered orders), rating breakdowns and owner replies
- **Favourites** and **saved addresses**
- **Owner analytics**: revenue, AOV, repeat-customer rate, cancel rate, daily series, top items, busiest hours
- **Scale**: annotated querysets (no N+1), DB indexes, optional pagination, request throttling

## Tech Stack

- **Django 5.1.7**: Web framework
- **Django REST Framework 3.15.0**: API development
- **Simple JWT 5.3.1**: JWT authentication
- **Django CORS Headers 4.3.1**: Cross-Origin Resource Sharing
- **Pillow 10.2.0**: Image processing
- **PostgreSQL** in production, SQLite fallback for local development
- **python-dotenv**: Environment variable management

## Project Structure

- **accounts**: users, loyalty, saved addresses
- **restaurants**: restaurants, menu items, reviews, favourites, meal planner, `seed_demo` command
- **orders**: orders, pricing engine (`orders/pricing.py`), promo codes, status timeline, chat, analytics
- **quickfood_backend**: project configuration

## API Endpoints

### Authentication and profile

- `POST /api/accounts/register/`: Register (`role`: `user` or `restaurant_owner`)
- `POST /api/accounts/token/`: Login and get JWT tokens
- `POST /api/accounts/token/refresh/`: Refresh JWT token
- `GET, PATCH /api/accounts/profile/`: Profile incl. `loyalty_points`, `loyalty_tier`, `next_tier` (role cannot be changed; passwords are hashed)
- `GET, POST, PATCH, DELETE /api/accounts/addresses/`: Saved addresses

### Restaurants

- `GET /api/restaurants/restaurant/`: List. Query params: `search`, `cuisine` (comma list), `dietary` (`vegetarian|vegan|gluten_free`), `open_now`, `free_delivery`, `price_level`, `min_rating`, `max_eta`, `favorites=1`, `owner=me`, `ordering` (`recommended|rating|fastest|delivery_fee|popular|newest|price_low`), `page` (opt-in pagination)
- `GET /api/restaurants/restaurant/{id}/`: Detail with menu, categories, popular items and rating breakdown
- `POST, PATCH, DELETE /api/restaurants/restaurant/{id}/`: Owner management (multipart for images)
- `POST /api/restaurants/restaurant/{id}/favorite/`: Toggle favourite
- `GET /api/restaurants/restaurant/cuisines/`: Cuisines with counts
- `GET /api/restaurants/meal-planner/?budget=25&people=2&dietary=vegan&restaurant={id}`: Budget Bites

### Menu items

- `GET /api/restaurants/menu-items/?restaurant={id}&search=`: List
- `POST, PATCH, DELETE /api/restaurants/menu-items/{id}/`: Owner management (category, dietary flags, spice level, calories)

### Reviews

- `GET /api/restaurants/reviews/?restaurant={id}` or `?mine=1`
- `POST /api/restaurants/reviews/`: `{order, rating, comment}` for a delivered order
- `POST /api/restaurants/reviews/{id}/reply/`: Owner reply

### Orders

- `GET /api/orders/?status=active|past|<status>`: List (filtered by user role)
- `POST /api/orders/quote/`: Price a cart without placing it
- `POST /api/orders/`: Place an order: `order_items`, `delivery_address`, `delivery_option`, `payment_method`, `promo_code`, `use_points`, `tip`, `scheduled_for`, `notes`, `contact_phone`
- `GET /api/orders/{id}/`: Detail with status timeline
- `PATCH /api/orders/{id}/`: Update status (restaurant owner, forward only)
- `POST /api/orders/{id}/cancel/`: Customer cancel while pending
- `POST /api/orders/{id}/reorder/`: Get the order back as a cart
- `GET, POST /api/orders/{id}/messages/`: Order chat
- `GET /api/orders/promos/`: Active promo codes
- `GET /api/orders/analytics/?days=30`: Owner analytics

## User Roles

The system supports different user roles:
- **User/Customer**: Can browse restaurants, place orders
- **Restaurant Owner**: Can manage their restaurants, menu items, and orders

## Setup and Installation

1. Clone the repository
```bash
git clone <repository-url>
cd quickfood_backend
```

2. Create and activate a virtual environment
```bash
python -m venv venv
venv\Scripts\activate
```

3. Install dependencies
```bash
pip install -r requirements.txt
```

4. Configure environment (`.env`)
```bash
DEBUG=True            # enables a local dev SECRET_KEY and SQLite when no DB credentials are set
# SECRET_KEY=...      # required in production
# DB_USER=... DB_PASSWORD=...  or  DATABASE_URL=postgres://...
```

5. Run migrations and load demo data
```bash
python manage.py migrate
python manage.py seed_demo   # demo_customer / demo_owner, password quickfood123
```

6. Create a superuser
```bash
python manage.py createsuperuser
```

7. Run the development server
```bash
python manage.py runserver
```

8. Base API will be run at http://localhost:8000

9. Run the tests
```bash
python manage.py test
```

## Authentication

The API uses JWT (JSON Web Token) for authentication. To access protected endpoints:

1. Obtain a token by logging in
2. Include the token in the Authorization header of your requests:
   `Authorization: Bearer <your_token>`

## Permissions

- Public endpoints: Restaurant listing, menu items viewing
- Protected endpoints: Creating/updating restaurants, menu items, and orders
- Role-based permissions: Different actions are allowed based on user roles

## Media Files

Restaurant and menu item images are stored in the `media/` directory and served at `/media/` URL path.
