import { createContext } from "react";
import type { AuthState } from "../types/auth";

/**
 * Interface defining the shape of the authentication context
 * Extends the base AuthState with authentication actions
 */
export interface AuthContextType extends AuthState {
  /**
   * Logs in a user with the provided token
   * @param token - JWT or authentication token
   * @param expiresInMinutes - Optional expiration time in minutes
   */
  login: (token: string, expiresInMinutes?: number) => void;
  logout: () => void;

  /**
   * Checks if the current user is authenticated
   * @returns true if user is authenticated, false otherwise
   */
  checkAuthStatus: () => boolean;
}

/**
 * React Context for managing authentication state throughout the application
 * Provides access to authentication status, user data, and auth actions
 *
 * Usage: Use with AuthProvider to wrap components that need auth state
 * Access via useAuth hook for type-safe context consumption
 */
export const AuthContext = createContext<AuthContextType | undefined>(
  undefined,
);
