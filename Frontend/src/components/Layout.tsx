import { Outlet } from "react-router-dom";
import Header from "./Header";
import Chatbot from "./Chatbot";

const Layout = () => {
  return (
    <div className="h-screen flex flex-col bg-background overflow-hidden">
      <Header />
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
      <Chatbot />
    </div>
  );
};

export default Layout;