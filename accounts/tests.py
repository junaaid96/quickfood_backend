from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

User = get_user_model()


class AccountTests(APITestCase):
    def test_register_login_and_profile(self):
        res = self.client.post('/api/accounts/register/', {
            'username': 'amy', 'email': 'amy@example.com', 'password': 'Str0ng-pass!', 'role': 'user',
            'first_name': 'Amy', 'last_name': 'Lee'}, format='json')
        self.assertEqual(res.status_code, 201)
        res = self.client.post('/api/accounts/token/', {'username': 'amy', 'password': 'Str0ng-pass!'})
        self.assertEqual(res.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        res = self.client.get('/api/accounts/profile/')
        self.assertEqual(res.data['loyalty_tier'], 'Bronze')

    def test_weak_password_rejected(self):
        res = self.client.post('/api/accounts/register/', {
            'username': 'bob', 'email': 'bob@example.com', 'password': '123', 'role': 'user',
            'first_name': 'Bob', 'last_name': 'Ray'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('password', res.data)

    def test_profile_update_hashes_password_and_keeps_role(self):
        user = User.objects.create_user('cat', 'cat@example.com', 'Str0ng-pass!', role='user')
        self.client.force_authenticate(user)
        res = self.client.patch('/api/accounts/profile/', {'password': 'An0ther-pass!', 'role': 'restaurant_owner'})
        self.assertEqual(res.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.role, 'user')
        self.assertTrue(user.check_password('An0ther-pass!'))

    def test_addresses_first_is_default(self):
        user = User.objects.create_user('dan', 'dan@example.com', 'Str0ng-pass!')
        self.client.force_authenticate(user)
        first = self.client.post('/api/accounts/addresses/', {'label': 'Home', 'line': '1 Main St'}).data
        self.assertTrue(first['is_default'])
        self.client.post('/api/accounts/addresses/', {'label': 'Work', 'line': '2 Office Rd', 'is_default': True})
        addresses = self.client.get('/api/accounts/addresses/').data
        self.assertEqual([a['label'] for a in addresses if a['is_default']], ['Work'])
