import React, { createContext, useContext, useEffect, useState } from 'react';
import { useUser, useAuth } from '@clerk/clerk-react';

const UserContext = createContext<{ principal: string | null; isReady: boolean }>({ principal: null, isReady: false });

export const UserProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, isLoaded } = useUser();
  const [isReady, setIsReady] = useState(false);
  
  useEffect(() => {
    if (isLoaded) {
      // Set the principal once user is loaded
      if (user?.primaryEmailAddress?.emailAddress) {
        localStorage.setItem('principal', user.primaryEmailAddress.emailAddress);
      }
      setIsReady(true);
    }
  }, [isLoaded, user]);

  return (
    <UserContext.Provider value={{ principal: localStorage.getItem('principal'), isReady }}>
      {isReady ? children : <div>Loading session...</div>}
    </UserContext.Provider>
  );
};

export const useAppUser = () => useContext(UserContext);