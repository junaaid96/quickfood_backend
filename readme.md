# QuickFood - Backend

This is the backend API for the QuickFood application, a food delivery platform that connects customers with restaurants.

The API is available at https://quickfood-backend-hoi3.onrender.com/
Frontend repo: https://github.com/junaaid96/quickfood_frontend 
Frontend live: https://quickfood-frontend.vercel.app/

## Project Overview

- Restaurant owners to manage their restaurants and menus
- Customers to browse restaurants, view menus, and place orders
- Authentication and authorization for different user roles

## Tech Stack

- **Django 5.1.7**: Web framework
- **Django REST Framework 3.15.0**: API development
- **Simple JWT 5.3.1**: JWT authentication
- **Django CORS Headers 4.3.1**: Cross-Origin Resource Sharing
- **Pillow 10.2.0**: Image processing
- **PostgreSQL**: Database
- **python-dotenv**: Environment variable management

## Project Structure

The project is organized into the following main apps:

- **accounts**: User authentication and management
- **restaurants**: Restaurant and menu item management
- **orders**: Order processing and management
- **quickfood_backend**: Main project configuration

## API Endpoints

### Authentication

- `POST /api/accounts/register/`: Register a new user
- `POST /api/accounts/token/`: Login and get JWT tokens
- `POST /api/accounts/token/refresh/`: Refresh JWT token

### User Profile

- `GET /api/accounts/profile/`: View user profile
- `PUT/PATCH /api/accounts/profile/`: Update user profile

### Restaurants

- `GET /api/restaurants/restaurant/`: List all restaurants
- `POST /api/restaurants/restaurant/`: Create a new restaurant (restaurant owners only)
- `GET /api/restaurants/restaurant/{id}/`: Get restaurant details
- `PUT/PATCH /api/restaurants/restaurant/{id}/`: Update restaurant (owner only)
- `DELETE /api/restaurants/restaurant/{id}/`: Delete restaurant (owner only)

### Menu Items

- `GET /api/restaurants/menu-items/`: List all menu items
- `GET /api/restaurants/menu-items/?restaurant={id}`: List menu items for a specific restaurant
- `POST /api/restaurants/menu-items/`: Create a new menu item (restaurant owners only)
- `GET /api/restaurants/menu-items/{id}/`: Get menu item details
- `PUT/PATCH /api/restaurants/menu-items/{id}/`: Update menu item (owner only)
- `DELETE /api/restaurants/menu-items/{id}/`: Delete menu item (owner only)

### Orders

- `GET /api/orders/`: List orders (filtered by user role)
- `POST /api/orders/`: Create a new order (customers only)
- `GET /api/orders/{id}/`: Get order details
- `PATCH /api/orders/{id}/`: Update order status (restaurant owners only)

## User Roles

The system supports different user roles:
- **User**: Can browse restaurants, place orders
- **Restaurant Owner**: Can manage their restaurants, menu items, and orders
- **Admin**: Has full access to the system

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

4. Run migrations
```bash
python manage.py migrate
```

5. Create a superuser
```bash
python manage.py createsuperuser
```

6. Run the development server
```bash
python manage.py runserver
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
