// API Configuration
// Automatically use relative URLs in production (same domain as frontend)
// Use localhost in development

const API_BASE_URL = process.env.NODE_ENV === 'production' 
  ? '' // Empty string means relative URLs (e.g., /api/auth/login)
  : 'http://127.0.0.1:8008';

export default API_BASE_URL;
