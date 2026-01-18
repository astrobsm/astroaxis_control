import React, { useState } from 'react';
import API_BASE_URL from './config';

function Login({ onLoginSuccess }) {
  const [isRegistering, setIsRegistering] = useState(false);
  const [showAttendance, setShowAttendance] = useState(false);
  
  // Login form state
  const [phoneNumber, setPhoneNumber] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('');
  
  // Registration form state
  const [regFullName, setRegFullName] = useState('');
  const [regPhone, setRegPhone] = useState('');
  const [regEmail, setRegEmail] = useState('');
  const [regRole, setRegRole] = useState('sales_staff');
  const [regPassword, setRegPassword] = useState('');
  const [regDepartment, setRegDepartment] = useState('');
  
  // Attendance form state
  const [attendancePin, setAttendancePin] = useState('');
  const [attendanceAction, setAttendanceAction] = useState('clock_in');
  const [attendanceNotes, setAttendanceNotes] = useState('');
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const roles = [
    { value: 'admin', label: 'Administrator' },
    { value: 'sales_staff', label: 'Sales Staff' },
    { value: 'marketer', label: 'Marketer' },
    { value: 'customer_care', label: 'Customer Care' },
    { value: 'production_staff', label: 'Production Staff' }
  ];

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    // Check if admin credentials
    if (phoneNumber === '08033328385' && password === 'NATISS') {
      try {
        const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ 
            email: 'admin@astroasix.com', 
            password: 'NATISS' 
          }),
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || 'Login failed');
        }

        localStorage.setItem('access_token', data.access_token);
        localStorage.setItem('user', JSON.stringify(data.user));
        onLoginSuccess(data.user);
        setLoading(false);
        return;
      } catch (err) {
        setError(err.message);
        setLoading(false);
        return;
      }
    }

    // Validate role selection for regular users
    if (!role || role === '') {
      setError('Please select your role before logging in');
      setLoading(false);
      return;
    }

    // For regular users, try to find by phone
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/login-phone`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ phone: phoneNumber, password, role }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || 'Invalid phone number, password, or role');
      }

      localStorage.setItem('access_token', data.access_token);
      localStorage.setItem('user', JSON.stringify(data.user));
      onLoginSuccess(data.user);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleRegistration = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setSuccess('');

    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/register`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          email: regEmail,
          password: regPassword,
          full_name: regFullName,
          phone: regPhone,
          role: regRole,
          department: regDepartment
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || 'Registration failed');
      }

      setSuccess(' Registration successful! Your account is pending admin approval. Contact admin at 08033328385 for activation.');
      
      // Clear form
      setRegFullName('');
      setRegPhone('');
      setRegEmail('');
      setRegPassword('');
      setRegDepartment('');
      
      // Switch back to login after 5 seconds
      setTimeout(() => {
        setIsRegistering(false);
        setSuccess('');
      }, 5000);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleQuickAttendance = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setSuccess('');

    try {
      const response = await fetch(`${API_BASE_URL}/api/attendance/quick-attendance`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          pin: attendancePin,
          action: attendanceAction,
          notes: attendanceNotes
        }),
      });

      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(data.message || 'Attendance action failed');
      }

      setSuccess(`✅ ${data.message}${data.staff_name ? ` - ${data.staff_name}` : ''}`);
      
      // Clear form
      setAttendancePin('');
      setAttendanceNotes('');
      
      // Reset after 3 seconds
      setTimeout(() => {
        setSuccess('');
      }, 3000);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div 
      className="min-h-screen flex items-center justify-center"
      style={{
        backgroundImage: 'url(/company-logo.png?v=20260118)',
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        backgroundRepeat: 'no-repeat',
        backgroundColor: 'rgba(0, 0, 0, 0.75)',
        backgroundBlendMode: 'overlay'
      }}
    >
      <div className="bg-white/95 backdrop-blur-sm rounded-2xl shadow-2xl p-8 w-full max-w-md">
        {/* Logo and Title */}
        <div className="text-center mb-6">
          <img 
            src="/company-logo.png?v=20260118" 
            alt="ASTRO-ASIX" 
            className="w-24 h-24 mx-auto mb-4 rounded-full shadow-lg border-4 border-indigo-100"
            onError={(e) => { e.target.style.display = 'none'; }}
          />
          <h1 className="text-3xl font-bold text-gray-800 mb-2">ASTRO-ASIX ERP</h1>
          <p className="text-gray-600">WoundCare Production & Sales</p>
        </div>

        {/* Toggle between Login, Registration, and Attendance */}
        <div className="flex mb-6 bg-gray-100 rounded-lg p-1 text-sm">
          <button
            onClick={() => { setIsRegistering(false); setShowAttendance(false); setError(''); setSuccess(''); }}
            className={`flex-1 py-2 rounded-md font-medium transition ${
              !isRegistering && !showAttendance
                ? 'bg-white shadow text-indigo-600' 
                : 'text-gray-600 hover:text-gray-800'
            }`}
          >
            Login
          </button>
          <button
            onClick={() => { setIsRegistering(true); setShowAttendance(false); setError(''); setSuccess(''); }}
            className={`flex-1 py-2 rounded-md font-medium transition ${
              isRegistering && !showAttendance
                ? 'bg-white shadow text-indigo-600' 
                : 'text-gray-600 hover:text-gray-800'
            }`}
          >
            Register
          </button>
          <button
            onClick={() => { setShowAttendance(true); setIsRegistering(false); setError(''); setSuccess(''); }}
            className={`flex-1 py-2 rounded-md font-medium transition ${
              showAttendance
                ? 'bg-white shadow text-green-600' 
                : 'text-gray-600 hover:text-gray-800'
            }`}
          >
            🕐 Attendance
          </button>
        </div>

        {/* Error and Success Messages */}
        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
             {error}
          </div>
        )}
        {success && (
          <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg text-green-700 text-sm">
            {success}
          </div>
        )}

        {showAttendance ? (
          // ATTENDANCE FORM (No login required)
          <form onSubmit={handleQuickAttendance}>
            <div className="mb-4">
              <label className="block text-gray-700 font-medium mb-2">Staff Clock PIN *</label>
              <input
                type="password"
                maxLength="4"
                value={attendancePin}
                onChange={(e) => setAttendancePin(e.target.value)}
                placeholder="Enter your 4-digit Clock PIN"
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent text-center text-2xl tracking-widest"
                required
              />
              <p className="text-xs text-gray-500 mt-1">Your 4-digit Clock PIN is shown in the Staff table</p>
            </div>

            <div className="mb-4">
              <label className="block text-gray-700 font-medium mb-2">Action *</label>
              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => setAttendanceAction('clock_in')}
                  className={`py-3 rounded-lg font-medium transition ${
                    attendanceAction === 'clock_in'
                      ? 'bg-green-600 text-white shadow-lg'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  🕐 Clock In
                </button>
                <button
                  type="button"
                  onClick={() => setAttendanceAction('clock_out')}
                  className={`py-3 rounded-lg font-medium transition ${
                    attendanceAction === 'clock_out'
                      ? 'bg-red-600 text-white shadow-lg'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  🕐 Clock Out
                </button>
              </div>
            </div>

            <div className="mb-6">
              <label className="block text-gray-700 font-medium mb-2">Notes (Optional)</label>
              <input
                type="text"
                value={attendanceNotes}
                onChange={(e) => setAttendanceNotes(e.target.value)}
                placeholder="Any notes..."
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className={`w-full font-medium py-3 rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed shadow-lg ${
                attendanceAction === 'clock_in'
                  ? 'bg-green-600 hover:bg-green-700 text-white'
                  : 'bg-red-600 hover:bg-red-700 text-white'
              }`}
            >
              {loading ? 'Processing...' : `${attendanceAction === 'clock_in' ? 'Clock In' : 'Clock Out'}`}
            </button>

            <div className="mt-4 p-3 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-800">
              <strong>Note:</strong> Use this for quick attendance tracking without logging in.
            </div>
          </form>
        ) : !isRegistering ? (
          // LOGIN FORM
          <form onSubmit={handleLogin}>
            <div className="mb-4">
              <label className="block text-gray-700 font-medium mb-2">Role *</label>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                required
              >
                <option value="">-- Select Your Role --</option>
                {roles.map(r => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </select>
            </div>

            <div className="mb-4">
              <label className="block text-gray-700 font-medium mb-2">Phone Number *</label>
              <input
                type="tel"
                value={phoneNumber}
                onChange={(e) => setPhoneNumber(e.target.value)}
                placeholder="e.g., 08033328385"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                required
              />
            </div>

            <div className="mb-6">
              <label className="block text-gray-700 font-medium mb-2">Password *</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                required
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-3 rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed shadow-lg"
            >
              {loading ? ' Logging in...' : ' Login'}
            </button>
          </form>
        ) : (
          // REGISTRATION FORM
          <form onSubmit={handleRegistration}>
            <div className="mb-4">
              <label className="block text-gray-700 font-medium mb-2">Full Name *</label>
              <input
                type="text"
                value={regFullName}
                onChange={(e) => setRegFullName(e.target.value)}
                placeholder="Enter full name"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                required
              />
            </div>

            <div className="mb-4">
              <label className="block text-gray-700 font-medium mb-2">Phone Number *</label>
              <input
                type="tel"
                value={regPhone}
                onChange={(e) => setRegPhone(e.target.value)}
                placeholder="e.g., 08012345678"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                required
              />
            </div>

            <div className="mb-4">
              <label className="block text-gray-700 font-medium mb-2">Email *</label>
              <input
                type="email"
                value={regEmail}
                onChange={(e) => setRegEmail(e.target.value)}
                placeholder="your.email@example.com"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                required
              />
            </div>

            <div className="mb-4">
              <label className="block text-gray-700 font-medium mb-2">Role *</label>
              <select
                value={regRole}
                onChange={(e) => setRegRole(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                required
              >
                {roles.filter(r => r.value !== 'admin').map(r => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </select>
            </div>

            <div className="mb-4">
              <label className="block text-gray-700 font-medium mb-2">Department</label>
              <input
                type="text"
                value={regDepartment}
                onChange={(e) => setRegDepartment(e.target.value)}
                placeholder="e.g., Sales, Production"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
            </div>

            <div className="mb-6">
              <label className="block text-gray-700 font-medium mb-2">Password *</label>
              <input
                type="password"
                value={regPassword}
                onChange={(e) => setRegPassword(e.target.value)}
                placeholder="Create a strong password"
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                required
                minLength="6"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-green-600 hover:bg-green-700 text-white font-medium py-3 rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed shadow-lg"
            >
              {loading ? ' Registering...' : ' Create Account'}
            </button>

            <div className="mt-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg text-sm text-yellow-800">
              <strong> Note:</strong> Your account needs admin approval before login. 
              Contact admin at <strong className="text-yellow-900">08033328385</strong> for approval.
            </div>
          </form>
        )}

        {/* Footer */}
        <div className="mt-6 text-center text-xs text-gray-500">
          <p> 2025 AstroBSM StockMaster. All rights reserved.</p>
        </div>
      </div>
    </div>
  );
}

export default Login;
