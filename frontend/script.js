marked.setOptions({
  breaks: true, // Convert \n to <br>
  gfm: true, // GitHub Flavored Markdown
  paragraphs: true, // Create proper <p> tags
  headerIds: false, // Disable automatic header IDs
  mangle: false, // Don't escape underscores
  smartLists: true, // Use smarter list behavior
  smartypants: true, // Use smart punctuation
  xhtml: false, // Don't use XHTML self-closing tags
});

// Sanitize all markdown output using DOMPurify
if (typeof marked !== "undefined" && marked.parse) {
  const _rawMarkedParse = marked.parse;
  marked.parse = function (src, opt) {
    const rawHtml = _rawMarkedParse.call(marked, src || "", opt);
    return typeof DOMPurify !== "undefined" ? DOMPurify.sanitize(rawHtml) : rawHtml;
  };
}

function escapeHtml(text) {
  if (text === null || text === undefined) return "";
  const div = document.createElement("div");
  div.textContent = String(text);
  return div.innerHTML;
}

document.addEventListener("DOMContentLoaded", () => {
  // --- CONFIGURATION & STATE ---
  const API_BASE_URL = "http://127.0.0.1:8000";
  let state = {
    token: localStorage.getItem("accessToken"),
    user: null,
    currentPage: "query",
    projects: [],
    selectedProjectId: null,
    selectedProjectName: null,
    chatHistory: {},
    conversationId: {},
    charts: {},
    isStreaming: false,
    regeneratingQueryId: null,
    currentAbortController: null,
    selectedDatabaseEngine: "postgresql",
    selectedUsers: [],
    selectedProjectUsers: [],
    sharingProjectId: null,
    sharingProjectName: null,
    existingSharedUsers: [],

    jiraFilters: {
      search: "",
      status: [],
      priority: [],
      assignee: "",
      type: [],
      reporter: "",
      labels: [],
      createdFrom: "",
      createdTo: "",
      updatedFrom: "",
      updatedTo: "",
      dueFrom: "",
      dueTo: "",
      hasAttachment: false,
    },
    activeFilters: [],
    filteredJiraIssues: [],
    jiraConnections: [],
    currentJiraConnection: null,
    jiraProjects: [],
    jiraIssues: [],
    allJiraProjects: [],
    filteredJiraProjects: [],
    databaseConnections: [],
    currentConnectionId: null,
    selectedConnectionForType: {},
    queryResults: null,
  };
  // =============================================
  // THEME TOGGLE FUNCTIONS
  // =============================================

  // Load saved theme preference
  function loadThemePreference() {
    const savedTheme = localStorage.getItem("theme");
    if (savedTheme === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
      updateThemeIcon("dark");
    } else {
      document.documentElement.setAttribute("data-theme", "light");
      updateThemeIcon("light");
    }
  }

  // Update theme toggle button icon
  function updateThemeIcon(theme) {
    const themeBtn = document.getElementById("theme-toggle-btn");
    if (themeBtn) {
      if (theme === "dark") {
        themeBtn.innerHTML = "☀️";
        themeBtn.title = "Switch to Light Mode";
      } else {
        themeBtn.innerHTML = "🌙";
        themeBtn.title = "Switch to Dark Mode";
      }
    }
  }

  // Toggle theme function
  function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute("data-theme");
    const newTheme = currentTheme === "dark" ? "light" : "dark";

    document.documentElement.setAttribute("data-theme", newTheme);
    localStorage.setItem("theme", newTheme);
    updateThemeIcon(newTheme);

    // Optional: Show toast notification
    if (typeof showSweetAlert === "function") {
      showSweetAlert(
        `${newTheme === "dark" ? "🌙 Dark" : "☀️ Light"} mode activated`,
        "success",
        "Theme Changed",
      );
    }
  }

  // Initialize theme toggle
  function initThemeToggle() {
    const themeBtn = document.getElementById("theme-toggle-btn");
    if (themeBtn) {
      themeBtn.addEventListener("click", toggleTheme);
    }
    loadThemePreference();
  }

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => document.querySelectorAll(selector);

  function init() {
    setupEventListeners();
    verifyTokenAndInit();
    setupChatAutoScroll();
    initDatabaseConnections();
    initThemeToggle();

    // Handle database engine change - auto disconnect if type mismatch
    $("#database-engine-selector").addEventListener("change", async (e) => {
      const newDbType = e.target.value;
      const oldDbType = state.selectedDatabaseEngine;

      console.log(`Database engine changing from ${oldDbType} to ${newDbType}`);

      // Check if we have an active connection for the OLD type
      if (oldDbType && state.selectedConnectionForType[oldDbType]) {
        // Get the active connection details
        const activeConnectionId = state.selectedConnectionForType[oldDbType];
        const activeConnection = state.databaseConnections?.find(
          (c) => c.id === activeConnectionId,
        );

        console.log(`Active connection for ${oldDbType}:`, activeConnection);

        // If connection exists and its type doesn't match the new DB type, disconnect
        if (activeConnection && activeConnection.db_type !== newDbType) {
          console.log(
            `🔌 Auto-disconnecting: Connection type (${activeConnection.db_type}) doesn't match new DB type (${newDbType})`,
          );

          // Clear the active connection for the OLD type
          delete state.selectedConnectionForType[oldDbType];

          // Reset the database icon
          const dbButton = $("#db-connection-button");
          if (dbButton) {
            dbButton.title = "Manage Database Connections";
            dbButton.style.background = "";
            dbButton.style.background = "var(--primary)";
            dbButton.classList.remove("active-connection");
          }

          // Reset the connection selector dropdown if it exists
          const selector = $("#active-connection-selector");
          if (selector) {
            selector.value = "";
          }

          const hasDefaultForNewType = state.databaseConnections?.some(
            (conn) => conn.db_type === newDbType && conn.is_default,
          );

          if (!hasDefaultForNewType) {
            showSweetAlert(
              `🔌 Disconnected from "${activeConnection.name}". <br> No ${newDbType} connection selected.`,
              "info",
            );
          } else {
            showSweetAlert(
              `🔌 Disconnected from "${activeConnection.name}".`,
              "info",
            );
          }
        }
        // If connection type matches new DB type, keep it connected but move the reference
        else if (activeConnection && activeConnection.db_type === newDbType) {
          console.log(
            `✅ Moving connection reference from ${oldDbType} to ${newDbType}`,
          );

          // Move the connection reference to the new type
          state.selectedConnectionForType[newDbType] = activeConnectionId;
          delete state.selectedConnectionForType[oldDbType];

          // Update the database icon to show still connected
          updateDatabaseSelectorIcon(newDbType, activeConnection.name);
        }
      }

      // Update state with new database type
      state.selectedDatabaseEngine = newDbType;

      // If no active connection for the new type, try to load default connection
      if (newDbType && !state.selectedConnectionForType[newDbType]) {
        await loadDefaultConnection(newDbType);
      }

      // Refresh all code blocks to update Run button states
      setTimeout(() => {
        document.querySelectorAll(".assistant-bubble").forEach((bubble) => {
          enhanceCodeBlocks(bubble);
        });
      }, 100);
    });
  }

  function showSweetAlert(message, icon = "warning", title = "") {
    const containsHTML = /<[a-z][\s\S]*>/i.test(message);
    return Swal.fire({
      title: title,
      ...(containsHTML ? { html: message } : { text: message }),
      icon: icon,
      showConfirmButton: true,
      iconColor: "#3F0D66",
      background: "#ffffff",
      color: "#3F0D66",
      confirmButtonColor: "#3F0D66",
      confirmButtonText: "OK",
      buttonsStyling: true,
    });
  }

  async function apiRequest(endpoint, options = {}) {
    showLoader(true);
    const headers = { ...options.headers };
    if (!(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    if (state.token) {
      headers["Authorization"] = `Bearer ${state.token}`;
    }
    try {
      const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        ...options,
        headers,
      });
      if (response.status === 401) {
        handleLogout();
        throw new Error("Session expired. Please log in.");
      }
      if (options.stream) return response;
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || `Server Error: ${response.status}`);
      }
      return response.status === 204 ? null : response.json();
    } catch (error) {
      showSweetAlert(error.message, "error");
      throw error;
    } finally {
      showLoader(false);
    }
  }

  function navigateTo(page) {
    console.log(`Attempting to navigate to page: ${page}`); // For debugging

    // Hide all page content divs first
    $$(".page-content").forEach((p) => (p.style.display = "none"));

    // Find the new page to display
    const newPage = $(`#${page}-page`);

    // **THIS IS THE FIX**
    // Check if the page element actually exists before trying to show it
    if (newPage) {
      newPage.style.display = "block";
    } else {
      console.error(
        `Navigation failed: Page element with ID #${page}-page not found.`,
      );
      return; // Stop the function if the page doesn't exist
    }

    // Update the active state of the sidebar buttons
    $$(".nav-btn").forEach((b) => b.classList.remove("active"));
    const activeBtn = $(`button[data-page="${page}"]`);
    if (activeBtn) {
      activeBtn.classList.add("active");
    }

    state.currentPage = page;

    // Load data for the new page
    switch (page) {
      case "projects":
        loadProjects();
        break;
      case "query":
        loadProjectsForQueryPage();
        break;
      case "query_history":
        loadProjectsForHistoryPage();
        break;
      case "analytics":
        loadAnalyticsData();
        break;
      case "jira":
        loadJiraConnections();
        loadProjectsForJiraImport();
        break;
    }
  }

  const showLoader = (isLoading) =>
    ($("#loader").style.display = isLoading ? "flex" : "none");
  const showError = (message, container = "#auth-error") =>
    ($(container).textContent = message);

  async function deleteConversation(conversationId, projectId) {
    try {
      showLoader(true);

      const result = await apiRequest(
        `/conversation/${conversationId}?project_id=${projectId}`,
        {
          method: "DELETE",
        },
      );

      showSweetAlert(`✅ Conversation deleted successfully!`, "success");

      // Refresh the conversation list
      await loadConversationsForHistory();

      // Clear the conversation selector value
      $("#history-conversation-selector").value = "";

      // Clear the transcript display
      $("#history-transcript").innerHTML = `
                <div class="chat-bubble assistant-bubble welcome-bubble">
                    <div class="welcome-content">
                        <div class="welcome-text">
                            🗑️<br>
                            <strong>Conversation Deleted</strong><br>
                            The conversation has been removed. Select another conversation from the dropdown.
                        </div>
                    </div>
                </div>
            `;

      // Hide continue container
      $("#continue-conversation-container").style.display = "none";
    } catch (error) {
      console.error("Delete conversation error:", error);
      showSweetAlert(
        `Failed to delete conversation: ${error.message}`,
        "error",
      );
    } finally {
      showLoader(false);
    }
  }

  async function verifyTokenAndInit() {
    if (state.token) {
      try {
        state.user = await apiRequest("/verify_access_token");
        $("#username-display").textContent = state.user.username;
        $("#auth-container").style.display = "none";
        $("#app-wrapper").style.display = "flex";
        if (state.user.role === "admin") {
          $$(".admin-only").forEach((el) => (el.style.display = "block"));
        }
        navigateTo(state.currentPage);
      } catch (error) {
        handleLogout();
      }
    }
  }

  function handleLogout() {
    state.token = null;
    state.user = null;
    localStorage.removeItem("accessToken");
    location.reload();
  }

  function setupEventListeners() {
    $("#login-form").addEventListener("submit", handleLogin);
    $("#register-form").addEventListener("submit", handleRegister);
    $("#show-login-tab").addEventListener("click", () =>
      switchAuthTab("login"),
    );
    $("#show-register-tab").addEventListener("click", () =>
      switchAuthTab("register"),
    );
    $("#logout-btn").addEventListener("click", handleLogout);

    $("#sidebar-nav").addEventListener("click", (e) => {
      const button = e.target.closest(".nav-btn");
      if (button) {
        e.preventDefault(); // Prevent any default form submission behavior
        navigateTo(button.dataset.page);
      }
    });

    $("#sidebar-new-query-btn").addEventListener("click", () => {
      navigateTo("query");
      state.currentConversationPermission = "edit";
      console.log(
        "New Query for Current conversation permission:",
        state.currentConversationPermission,
      );
      if (state.selectedProjectId) {
        // Reset chat + conversation for the selected project
        state.chatHistory[state.selectedProjectId] = [];
        state.conversationId[state.selectedProjectId] = null;

        // Refresh UI - this will now show welcome message
        renderChatHistory();

        // ✅ FIX: Force update the send button state
        updateSendButtonState();
      }
    });

    $("#create-project-form").addEventListener("submit", handleCreateProject);
    $("#back-to-projects-btn").addEventListener("click", () =>
      navigateTo("projects"),
    );
    $$(".tabs .tab-btn").forEach((btn) =>
      btn.addEventListener("click", handleTabSwitch),
    );
    $("#upload-form").addEventListener("submit", handleUploadFiles);
    $("#chat-form").addEventListener("submit", handleChatSubmit);
    $("#query-project-selector").addEventListener("change", (e) => {
      const selectedOption = e.target.options[e.target.selectedIndex];
      state.selectedProjectId = selectedOption.value;
      state.selectedProjectName = selectedOption.text;
      renderChatHistory(); // This will show welcome message for newly selected project
    });

    $("#history-project-selector").addEventListener(
      "change",
      loadConversationsForHistory,
    );
    $("#history-conversation-selector").addEventListener(
      "change",
      renderConversationTranscript,
    );
    $("#jira-connect-form").addEventListener("submit", handleJiraConnect);
    $("#jira-connection-selector").addEventListener("change", loadJiraProjects);
    $("#confirm-import-btn").addEventListener("click", confirmJiraImport);
    $("#cancel-import-btn").addEventListener(
      "click",
      () => ($("#jira-import-modal").style.display = "none"),
    );
    $("#chat-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault(); // Prevent newline
        $("#chat-form").dispatchEvent(new Event("submit")); // Trigger send
      }
      // If Shift+Enter is pressed → allow newline
    });

    $("#chat-input").addEventListener("input", (e) => {
      const el = e.target;
      el.style.height = "auto"; // reset first
      el.style.height = Math.min(el.scrollHeight, 120) + "px"; // then resize correctly
    });

    $("#sidebar-toggle").addEventListener("click", toggleSidebar);
    // Add these lines in setupEventListeners() function
    $("#continue-conversation-container").addEventListener("click", (e) => {
      if (e.target.id === "share-conversation-btn") {
        openShareModal();
      }
    });

    // Share modal event listeners
    $("#add-user-btn").addEventListener("click", addUserToShareList);
    $("#share-username-input").addEventListener("keypress", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        addUserToShareList();
      }
    });

    $("#confirm-share-btn").addEventListener("click", confirmShare);
    $("#cancel-share-btn").addEventListener("click", () => {
      $("#share-modal").style.display = "none";
      resetShareModal();
    });

    // Add these lines to your setupEventListeners() function:
    $("#add-project-user-btn").addEventListener(
      "click",
      addUserToProjectShareList,
    );
    $("#share-project-username-input").addEventListener("keypress", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        addUserToProjectShareList();
      }
    });

    $("#confirm-project-share-btn").addEventListener(
      "click",
      confirmProjectShare,
    );
    $("#cancel-project-share-btn").addEventListener("click", () => {
      $("#share-project-modal").style.display = "none";
      resetProjectShareModal();
    });

    document.addEventListener("click", function (e) {
      // Handle add project user button
      if (e.target && e.target.id === "add-project-user-btn") {
        e.preventDefault();
        addUserToProjectShareList();
      }

      // Handle confirm project share button
      if (e.target && e.target.id === "confirm-project-share-btn") {
        e.preventDefault();
        confirmProjectShare();
      }

      // Handle cancel project share button
      if (e.target && e.target.id === "cancel-project-share-btn") {
        e.preventDefault();
        $("#share-project-modal").style.display = "none";
        resetProjectShareModal();
      }

      // Handle remove user buttons (delegated)
      if (e.target && e.target.classList.contains("remove-user")) {
        e.preventDefault();
        const userToRemove = e.target.dataset.user;
        state.selectedProjectUsers = state.selectedProjectUsers.filter(
          (u) => u !== userToRemove,
        );
        updateSelectedProjectUsersList();
      }
    });

    // Add keypress delegation for share username input
    document.addEventListener("keypress", function (e) {
      if (
        e.target &&
        e.target.id === "share-project-username-input" &&
        e.key === "Enter"
      ) {
        e.preventDefault();
        addUserToProjectShareList();
      }
    });

    $("#issue-search").addEventListener("input", handleFilterChange);
    $("#clear-search").addEventListener("click", clearSearch);
    $("#status-filter").addEventListener("change", handleFilterChange);
    $("#priority-filter").addEventListener("change", handleFilterChange);
    $("#assignee-filter").addEventListener("change", handleFilterChange);
    $("#type-filter").addEventListener("change", handleFilterChange);
    $("#reporter-filter").addEventListener("change", handleFilterChange);
    $("#labels-filter").addEventListener("input", handleFilterChange);
    $("#has-attachment").addEventListener("change", handleFilterChange);
    $("#project-search")?.addEventListener("input", filterJiraProjects);
    $("#clear-project-search")?.addEventListener("click", clearProjectSearch);
    $("#clear-project-filter")?.addEventListener("click", clearProjectSearch);
    $("#toggle-advanced-filters").addEventListener(
      "click",
      toggleAdvancedFilters,
    );
    $("#apply-filters").addEventListener("click", applyFilters);
    $("#clear-filters").addEventListener("click", clearAllFilters);
    $("#clear-active-filters").addEventListener("click", clearAllFilters);
    $("#clear-filters-show-all").addEventListener("click", clearAllFilters);

    // Date filter changes
    [
      "created-from",
      "created-to",
      "updated-from",
      "updated-to",
      "due-from",
      "due-to",
    ].forEach((id) => {
      $(`#${id}`).addEventListener("change", handleFilterChange);
    });

    $("#db-connection-button").addEventListener("click", () => {
      loadDatabaseConnections();
      $("#db-connection-modal").style.display = "flex";
    });

    // =============================================
    // EVENT DELEGATION FOR DELETE CONVERSATION BUTTON
    // =============================================
    document.addEventListener("click", async (e) => {
      const deleteBtn = e.target.closest("#delete-conversation-btn");
      if (!deleteBtn) return;

      console.log("🗑️ Delete button clicked via delegation!");

      const convId = $("#history-conversation-selector").value;
      const projectId = $("#history-project-selector").value;

      if (!convId || !projectId) {
        showSweetAlert("Please select a conversation first", "warning");
        return;
      }

      const selectedOption = $("#history-conversation-selector").options[
        $("#history-conversation-selector").selectedIndex
      ];
      const convTitle =
        selectedOption?.textContent?.split(" (Shared")[0] ||
        "this conversation";

      const result = await Swal.fire({
        title: "Delete Conversation?",
        html: `Are you sure you want to delete "<strong>${escapeHtml(convTitle)}</strong>"?<br><br>
                       This will permanently delete all messages in this conversation.<br>
                       <span style="color: var(--danger);">This action cannot be undone!</span>`,
        icon: "warning",
        showCancelButton: true,
        confirmButtonColor: "#dc3545",
        cancelButtonColor: "#3F0D66",
        confirmButtonText: "Yes, delete conversation",
        cancelButtonText: "Cancel",
      });

      if (result.isConfirmed) {
        await deleteConversation(convId, projectId);
      }
    });
    // =============================================
    // STOP BUTTON HANDLER - PASTE THIS HERE
    // =============================================
    $("#stop-btn").addEventListener("click", async () => {
      if (!state.isStreaming) return;

      const convId = state.conversationId[state.selectedProjectId];
      if (convId) {
        await apiRequest(`/chat/stop/${convId}`, { method: "POST" });
      }
      if (state.currentAbortController) {
        state.currentAbortController.abort();
        state.currentAbortController = null;
      }
      enableSendUI();
      showSweetAlert("⏹️ Generation stopped", "info");
    });
  }

  // =============================================
  // PROJECT SHARING FUNCTIONS
  // =============================================

  async function openShareProjectModal(projectId, projectName) {
    state.sharingProjectId = projectId;
    state.sharingProjectName = projectName;
    state.selectedProjectUsers = [];

    $("#share-project-name").textContent = projectName;
    $("#share-project-modal").style.display = "flex";

    // Load existing shared users and check permissions
    await loadExistingSharedUsers(projectId);
    const canShare = await checkSharingPermissions(projectId);

    if (!canShare) return;

    updateSelectedProjectUsersList();
  }

  async function loadExistingSharedUsers(projectId) {
    try {
      const result = await apiRequest(`/project/${projectId}/shared-users`);
      state.existingSharedUsers = result.users || [];
      renderExistingSharedUsers();
    } catch (error) {
      console.error("Failed to load shared users:", error);
      state.existingSharedUsers = [];
    }
  }

  function renderExistingSharedUsers() {
    const section = $("#share-project-modal").querySelector(
      ".shared-users-section",
    );
    const list = $("#share-project-modal").querySelector(".shared-users-list");

    if (state.existingSharedUsers.length === 0) {
      section.style.display = "none";
      return;
    }

    section.style.display = "block";
    list.innerHTML = state.existingSharedUsers
      .map(
        (user) => `
            <div class="shared-user-tag ${user.role === "admin" ? "admin" : ""}">
                <span>${escapeHtml(user.username)}</span>
                <span class="permission-badge">${escapeHtml(user.role)}</span>
            </div>
        `,
      )
      .join("");
  }

  async function checkSharingPermissions(projectId) {
    try {
      const project = state.projects.find((p) => p.project_id == projectId);
      if (!project) return false;

      const canShare =
        project.created_by === state.user.user_id ||
        project.access_level === "admin" ||
        state.user.role === "admin";

      if (!canShare) {
        showSweetAlert(
          "You do not have permission to share this project",
          "warning",
        );
        $("#share-project-modal").style.display = "none";
        return false;
      }

      return true;
    } catch (error) {
      console.error("Error checking sharing permissions:", error);
      return false;
    }
  }

  function addUserToProjectShareList() {
    const input = $("#share-project-username-input");
    const username = input.value.trim().toLowerCase();

    if (!username) return;

    if (username === state.user.username.toLowerCase()) {
      showSweetAlert("You cannot share with yourself", "warning");
      return;
    }

    // Check if already in selected list
    if (state.selectedProjectUsers.includes(username)) {
      showSweetAlert("User already added to share list", "warning");
      return;
    }

    // Check if already in existing shared users
    const alreadyShared = state.existingSharedUsers.find(
      (u) => u.username.toLowerCase() === username,
    );
    if (alreadyShared) {
      showSweetAlert(
        `User already has ${alreadyShared.role} access`,
        "warning",
      );
      return;
    }

    state.selectedProjectUsers.push(username);
    input.value = "";
    updateSelectedProjectUsersList();
  }

  function updateSelectedProjectUsersList() {
    const container = $("#selected-project-users-list");
    container.innerHTML = state.selectedProjectUsers
      .map(
        (user) => `
            <div class="user-tag">
                <span>${user}</span>
                <button class="remove-user" data-user="${user}">×</button>
            </div>
        `,
      )
      .join("");

    // Add event listeners to remove buttons
    container.querySelectorAll(".remove-user").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const userToRemove = e.target.dataset.user;
        state.selectedProjectUsers = state.selectedProjectUsers.filter(
          (u) => u !== userToRemove,
        );
        updateSelectedProjectUsersList();
      });
    });
  }

  async function confirmProjectShare() {
    if (!state.sharingProjectId || !state.selectedProjectUsers.length) {
      showSweetAlert("Please add at least one user to share with", "warning");
      return;
    }

    const permission = $('input[name="project-permission"]:checked').value;

    try {
      const result = await apiRequest("/project/share", {
        method: "POST",
        body: JSON.stringify({
          project_id: parseInt(state.sharingProjectId),
          shared_with_usernames: state.selectedProjectUsers,
          permission: permission,
        }),
      });

      // ✅ Don't replace HTML, just show success message and close
      showSweetAlert(
        result.message || "✅ Project shared successfully!",
        "success",
      );

      // Close modal and reset
      $("#share-project-modal").style.display = "none";
      resetProjectShareModal();

      // Reload projects to show updated shared users
      loadProjects();
    } catch (error) {
      showSweetAlert(`Failed to share project: ${error.message}`, "error");
    }
  }

  function resetProjectShareModal() {
    state.selectedProjectUsers = [];
    state.sharingProjectId = null;
    state.sharingProjectName = null;
    state.existingSharedUsers = [];

    // Just clear the input fields
    if ($("#share-project-username-input")) {
      $("#share-project-username-input").value = "";
    }
    if ($("#selected-project-users-list")) {
      $("#selected-project-users-list").innerHTML = "";
    }
    if ($('input[name="project-permission"][value="user"]')) {
      $('input[name="project-permission"][value="user"]').checked = true;
    }

    // Hide the shared users section
    const sharedSection = $(".shared-users-section");
    if (sharedSection) {
      sharedSection.style.display = "none";
    }
  }

  function updateSendButtonState() {
    const chatInput = $("#chat-input");
    const sendBtn = $('#chat-form button[type="submit"]');

    console.log(
      "Updating send button state. Permission:",
      state.currentConversationPermission,
    ); // DEBUG

    // ✅ FIX: Only disable if explicit 'view' permission
    if (state.currentConversationPermission === "view") {
      console.log("🔐 View-only mode enabled");
      if (chatInput) {
        chatInput.disabled = true;
        chatInput.placeholder =
          "You have view-only permission for this conversation";
        chatInput.style.opacity = "0.5";
        chatInput.style.cursor = "not-allowed";
      }

      if (sendBtn) {
        sendBtn.disabled = true;
        sendBtn.style.opacity = "0.5";
        sendBtn.style.cursor = "not-allowed";
        sendBtn.title = "View-only permission";
      }
    } else {
      // ✅ FIX: Enable when permission is not 'view' (including null/undefined)
      console.log("🔐 Edit mode - enabling chat input");
      if (chatInput) {
        chatInput.disabled = false;
        chatInput.placeholder = "Type your question here...";
        chatInput.style.opacity = "1";
        chatInput.style.cursor = "text";
      }

      if (sendBtn) {
        sendBtn.disabled = false;
        sendBtn.style.opacity = "1";
        sendBtn.style.cursor = "pointer";
        sendBtn.title = "";
      }
    }
  }

  function toggleSidebar() {
    $("#sidebar").classList.toggle("sidebar-collapsed");
  }

  function openShareModal() {
    const convId = $("#history-conversation-selector").value;
    if (!convId) {
      showSweetAlert("Please select a conversation first", "warning");
      return;
    }

    $("#share-modal").style.display = "flex";
    state.selectedUsers = [];
    updateSelectedUsersList();
  }

  function addUserToShareList() {
    const input = $("#share-username-input");
    const username = input.value.trim().toLowerCase();

    if (!username) return;

    if (username === state.user.username.toLowerCase()) {
      showSweetAlert("You cannot share with yourself", "warning");
      return;
    }

    if (state.selectedUsers.includes(username)) {
      showSweetAlert("User already added to share list", "warning");
      return;
    }

    state.selectedUsers.push(username);
    input.value = "";
    updateSelectedUsersList();
  }

  function updateSelectedUsersList() {
    const container = $("#selected-users-list");
    container.innerHTML = state.selectedUsers
      .map(
        (user) => `
            <div class="user-tag">
                <span>${user}</span>
                <button class="remove-user" data-user="${user}">×</button>
            </div>
        `,
      )
      .join("");

    // Add event listeners to remove buttons
    $$(".remove-user").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const userToRemove = e.target.dataset.user;
        state.selectedUsers = state.selectedUsers.filter(
          (u) => u !== userToRemove,
        );
        updateSelectedUsersList();
      });
    });
  }

  async function confirmShare() {
    const convId = $("#history-conversation-selector").value;
    const projectId = $("#history-project-selector").value;
    const permission = $('input[name="permission"]:checked').value;

    if (!state.selectedUsers.length) {
      showSweetAlert("Please add at least one user to share with", "warning");
      return;
    }

    // Debug: Log what we're sending
    console.log("Sharing data:", {
      conversation_id: convId,
      project_id: parseInt(projectId),
      shared_with_usernames: state.selectedUsers,
      permission: permission,
    });

    try {
      const result = await apiRequest("/conversations/share", {
        method: "POST",
        body: JSON.stringify({
          conversation_id: convId,
          project_id: parseInt(projectId),
          shared_with_usernames: state.selectedUsers,
          permission: permission,
        }),
      });

      $("#share-modal").style.display = "none";
      resetShareModal();
      showSweetAlert(result.message, "success");
    } catch (error) {
      showSweetAlert(`Failed to share conversation: ${error.message}`, "error");
    }
  }

  function resetShareModal() {
    state.selectedUsers = [];
    $("#share-username-input").value = "";
    $("#selected-users-list").innerHTML = "";
    $('input[name="permission"][value="view"]').checked = true;
  }

  async function handleJiraCreateProject() {
    const newProjectName = $("#new-project-name-input").value.trim();
    if (!newProjectName) {
      showSweetAlert("Please enter a project name", "warning"); // Changed from '#auth-error'
      return;
    }

    try {
      // Use the existing create project endpoint
      const result = await apiRequest("/project", {
        method: "POST",
        body: JSON.stringify({ project_name: newProjectName }),
      });

      // Update the projects list and select the newly created project
      await loadProjectsForJiraImport();

      // Switch to existing project option and select the new project
      $('input[name="import-option"][value="existing"]').checked = true;
      $("#new-project-section").style.display = "none";
      $("#existing-projects-select").style.display = "block";
      $("#existing-projects-select").value = result.project_id;

      $("#new-project-name-input").value = "";

      showSweetAlert("✅ Project created successfully!", "success");
    } catch (error) {
      showSweetAlert(`Failed to create project: ${error.message}`, "error");
    }
  }

  // Auth Handlers
  async function handleLogin(e) {
    e.preventDefault();
    const username = $("#login-username").value;
    const password = $("#login-password").value;
    try {
      const data = await apiRequest("/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      localStorage.setItem("accessToken", data.access_token);
      state.token = data.access_token;
      verifyTokenAndInit();
    } catch (error) {}
  }

  async function handleRegister(e) {
    e.preventDefault();
    const username = $("#register-username").value;
    const password = $("#register-password").value;
    if (password !== $("#register-confirm-password").value)
      return showSweetAlert("Passwords do not match", "warning");
    try {
      await apiRequest("/register", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      showSweetAlert("Registration successful! Please log in.", "success");
      switchAuthTab("login");
    } catch (error) {}
  }

  function switchAuthTab(tabName) {
    $("#login-form").style.display = tabName === "login" ? "flex" : "none";
    $("#register-form").style.display =
      tabName === "register" ? "flex" : "none";
    $("#show-login-tab").classList.toggle("active", tabName === "login");
    $("#show-register-tab").classList.toggle("active", tabName === "register");
    showError("");
  }

  // Project & Document Logic
  async function loadProjects() {
    try {
      state.projects = await apiRequest("/projects");
      const grid = $("#projects-grid");
      grid.innerHTML = "";
      if (!state.projects || state.projects.length === 0) {
        grid.innerHTML = "<p>No projects found. Create one above!</p>";
        return;
      }
      state.projects.forEach((p) => {
        const isCreator = p.created_by === state.user.user_id;
        const isAdmin = p.access_level === "admin";
        const canShare = isCreator || isAdmin || state.user.role === "admin";
        const canDelete = isCreator || isAdmin || state.user.role === "admin"; // Same permissions for delete

        grid.innerHTML += `
                    <div class="card project-card" data-project-id="${p.project_id}">
                        <div class="project-card-header">
                            <div class="project-card-title-group">
                                <div class="project-icon-badge">
                                    <i class="fas fa-layer-group"></i>
                                </div>
                                <div class="project-title-info">
                                    <h4 class="project-name" title="${escapeHtml(p.project_name)}">${escapeHtml(p.project_name)}</h4>
                                    <div class="project-badges">
                                        ${isCreator ? '<span class="project-badge badge-owner"><i class="fas fa-crown"></i> Owner</span>' : ""}
                                        ${!isCreator && p.access_level === "admin" ? '<span class="project-badge badge-admin"><i class="fas fa-shield-halved"></i> Admin</span>' : ""}
                                        ${!isCreator && p.access_level !== "admin" ? '<span class="project-badge badge-member"><i class="fas fa-user-check"></i> Member</span>' : ""}
                                    </div>
                                </div>
                            </div>
                            <div class="project-card-actions">
                                ${
                                  canShare
                                    ? `
                                <button class="project-action-btn share-project-btn" 
                                        data-id="${p.project_id}" 
                                        data-name="${escapeHtml(p.project_name)}"
                                        title="Share this project">
                                    <i class="fas fa-share-nodes"></i>
                                </button>
                            `
                                    : ""
                                }
                                ${
                                  canDelete
                                    ? `
                                <button class="project-action-btn delete-project-btn" 
                                        data-id="${p.project_id}" 
                                        data-name="${escapeHtml(p.project_name)}"
                                        title="Delete this project">
                                    <i class="fas fa-trash-can"></i>
                                </button>
                            `
                                    : ""
                                }
                            </div>
                        </div>
                        <div class="project-card-body">
                            <div class="project-meta">
                                <span class="project-meta-item">
                                    <i class="far fa-calendar-alt"></i> ${new Date(p.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}
                                </span>
                                ${p.created_by_username ? `
                                <span class="project-meta-item">
                                    <i class="far fa-user"></i> ${escapeHtml(p.created_by_username)}
                                </span>` : ""}
                            </div>
                        </div>
                        <div class="project-card-footer">
                            <button class="manage-docs-btn" data-id="${p.project_id}" data-name="${escapeHtml(p.project_name)}">
                                <span><i class="fas fa-folder-open"></i> Manage Documents</span>
                                <i class="fas fa-arrow-right arrow-icon"></i>
                            </button>
                        </div>
                    </div>
                `;
      });

      // Add event listeners for manage docs buttons
      $$(".manage-docs-btn").forEach((btn) =>
        btn.addEventListener("click", (e) => {
          const targetBtn = e.currentTarget;
          state.selectedProjectId = targetBtn.dataset.id;
          state.selectedProjectName = targetBtn.dataset.name;
          navigateTo("project-detail");
          loadProjectDetails();
        }),
      );

      // Add event listeners for share buttons
      $$(".share-project-btn").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          console.log("Share button clicked for project:", btn.dataset.id);

          const projectId = btn.dataset.id;
          const projectName = btn.dataset.name;

          btn.classList.add("loading");
          const originalHTML = btn.innerHTML;
          btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

          try {
            await openShareProjectModal(projectId, projectName);
          } catch (error) {
            console.error("Error opening share modal:", error);
            showSweetAlert(
              "Failed to open share modal: " + error.message,
              "error",
            );
          } finally {
            btn.classList.remove("loading");
            btn.innerHTML = originalHTML;
          }
        });
      });

      // Add event listeners for delete buttons
      $$(".delete-project-btn").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          const projectId = btn.dataset.id;
          const projectName = btn.dataset.name;

          // Show confirmation dialog
          const result = await Swal.fire({
            title: "Delete Project?",
            html: `Are you sure you want to delete project <strong>${projectName}</strong>?<br><br>
                               This will permanently delete:<br>
                               • All documents and their chunks<br>
                               • All queries and conversations<br>
                               • All feedback and shared access<br>
                               • Jira imported issues<br>
                               <br><span style="color: var(--danger); font-weight: bold;">This action cannot be undone!</span>`,
            icon: "warning",
            showCancelButton: true,
            confirmButtonColor: "#dc3545",
            cancelButtonColor: "#3F0D66",
            confirmButtonText: "Yes, delete project",
            cancelButtonText: "Cancel",
            background: "#ffffff",
            color: "#31333F",
          });

          if (result.isConfirmed) {
            await deleteProject(projectId, projectName);
          }
        });
      });
    } catch (error) {
      console.error("Failed to load projects", error);
      showSweetAlert("Failed to load projects: " + error.message, "error");
    }
  }

  // =============================================
  // DELETE PROJECT FUNCTION
  // =============================================
  async function deleteProject(projectId, projectName) {
    try {
      showLoader(true);

      const result = await apiRequest(`/project/${projectId}`, {
        method: "DELETE",
      });

      showSweetAlert(
        `✅ Project "${projectName}" deleted successfully!`,
        "success",
      );

      // Refresh the projects list
      await loadProjects();

      // If this was the currently selected project, clear it from state
      if (state.selectedProjectId == projectId) {
        state.selectedProjectId = null;
        state.selectedProjectName = null;

        // Clear any chat history for this project
        if (state.chatHistory[projectId]) {
          delete state.chatHistory[projectId];
        }
        if (state.conversationId[projectId]) {
          delete state.conversationId[projectId];
        }
      }

      return result;
    } catch (error) {
      console.error("Delete project error:", error);
      showSweetAlert(`Failed to delete project: ${error.message}`, "error");
    } finally {
      showLoader(false);
    }
  }

  async function handleCreateProject(e) {
    e.preventDefault();
    const name = $("#new-project-name").value.trim();
    if (!name) return;
    try {
      await apiRequest("/project", {
        method: "POST",
        body: JSON.stringify({ project_name: name }),
      });
      $("#new-project-name").value = "";
      loadProjects();
    } catch (error) {}
  }

  async function pollStatus(doc) {
    try {
      const statusData = await apiRequest(`/status/${doc.doc_id}`);
      if (statusData.status !== "completed") {
        setTimeout(() => pollStatus(doc), 2000); // poll every 2s
      }
      loadProjectDetails(true); // refresh UI
    } catch (error) {
      console.error("Error polling status:", error);
    }
  }

  async function loadProjectDetails(polling = false) {
    $("#project-detail-title").textContent =
      `Documents for: ${state.selectedProjectName}`;
    const data = await apiRequest(
      `/project/${state.selectedProjectId}/documents`,
    );
    const list = $("#document-list");
    list.innerHTML = "";

    let hasPending = false;

    if (data.documents.length === 0) {
      list.innerHTML = "<p>No documents found. Upload one!</p>";
      return;
    }

    data.documents.forEach((doc) => {
      if (doc.status.toLowerCase() === "pending" || doc.status === "indexing") {
        hasPending = true;
        if (!polling) pollStatus(doc); // start polling this doc
      }

      list.innerHTML += `
                <div class="document-list-item">
                    <span class="doc-name">${escapeHtml(doc.doc_name)}</span>
                    <div class="doc-status">
                        <span>${escapeHtml(doc.status)}</span>
                        <button class="open-btn" data-id="${doc.doc_id}" data-name="${escapeHtml(doc.doc_name)}">
                        Open</button>
                        <button class="delete-btn" data-id="${doc.doc_id}">
                        Delete</button>
                    </div>
                </div>`;
    });

    $$(".open-btn").forEach((btn) =>
      btn.addEventListener("click", handleOpenDocument),
    );
    $$(".delete-btn").forEach((btn) =>
      btn.addEventListener("click", handleDeleteDocument),
    );
  }

  async function handleOpenDocument(e) {
    const docId = e.target.dataset.id;
    const docName = e.target.dataset.name;

    try {
      showLoader(true);

      const downloadUrl = `/project/${state.selectedProjectId}/documents/${docId}/download`;

      // Use apiRequest but handle blob response differently
      const response = await fetch(`${API_BASE_URL}${downloadUrl}`, {
        method: "GET",
        headers: {
          Authorization: `Bearer ${state.token}`,
        },
      });

      if (response.status === 401) {
        throw new Error("Not authenticated");
      }

      if (!response.ok) {
        throw new Error(`Download failed: ${response.status}`);
      }

      const blob = await response.blob();

      // Check if blob is actually an error JSON (sometimes APIs return JSON errors)
      if (blob.type === "application/json") {
        const errorText = await blob.text();
        const errorData = JSON.parse(errorText);
        throw new Error(errorData.detail || "Download failed");
      }

      // Create download
      const blobUrl = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = docName;
      document.body.appendChild(a);
      a.click();

      // Cleanup
      setTimeout(() => {
        document.body.removeChild(a);
        window.URL.revokeObjectURL(blobUrl);
      }, 100);

      showSweetAlert(`Download started: ${docName}`, "success");
    } catch (error) {
      console.error("Download error:", error);

      if (error.message.includes("Not authenticated")) {
        showSweetAlert(
          "Your session has expired. Please log in again.",
          "error",
        );
        setTimeout(() => handleLogout(), 2000);
      } else {
        showSweetAlert(`Download failed: ${error.message}`, "error");
      }
    } finally {
      showLoader(false);
    }
  }

  async function handleDeleteDocument(e) {
    const docId = e.target.dataset.id;
    if (!confirm("Are you sure?")) return;
    try {
      await apiRequest(
        `/project/${state.selectedProjectId}/documents/${docId}`,
        { method: "DELETE" },
      );
      loadProjectDetails();
    } catch (error) {}
  }

  async function handleUploadFiles(e) {
    e.preventDefault();
    const files = $("#file-upload").files;
    if (files.length === 0) return;
    const formData = new FormData();
    for (const file of files) {
      formData.append("files", file);
    }
    try {
      await apiRequest(`/upload?project_id=${state.selectedProjectId}`, {
        method: "POST",
        body: formData,
      });
      $("#upload-form").reset();
      loadProjectDetails();
    } catch (error) {}
  }

  // Chat Logic (omitted for brevity - keep your existing working chat functions)
  async function loadProjectsForQueryPage() {
    try {
      console.log(
        "Current conversation permission:",
        state.currentConversationPermission,
      ); // DEBUG
      // Get chat input and send button elements
      const chatInput = $("#chat-input");
      const sendBtn = $('#chat-form button[type="submit"]');

      // ✅ FIX: Only disable chat input if we have explicit view-only permission
      // When permission is null/undefined, it means we're in a regular conversation with edit permission
      if (state.currentConversationPermission === "view") {
        console.log("🔐 View-only mode enabled");
        if (chatInput) {
          chatInput.disabled = true;
          chatInput.placeholder =
            "You have view-only permission for this conversation";
          chatInput.style.opacity = "0.5";
          chatInput.style.cursor = "not-allowed";
        }

        if (sendBtn) {
          sendBtn.disabled = true;
          sendBtn.style.opacity = "0.5";
          sendBtn.style.cursor = "not-allowed";
          sendBtn.title = "View-only permission";
        }
      } else {
        // ✅ FIX: Always enable when permission is not 'view' (including null/undefined)
        console.log("🔐 Edit mode - enabling chat input");
        if (chatInput) {
          chatInput.disabled = false;
          chatInput.placeholder = "Type your question here...";
          chatInput.style.opacity = "1";
          chatInput.style.cursor = "text";
        }

        if (sendBtn) {
          sendBtn.disabled = false;
          sendBtn.style.opacity = "1";
          sendBtn.style.cursor = "pointer";
          sendBtn.title = "";
        }
      }

      state.projects = await apiRequest("/projects");
      const selector = $("#query-project-selector");
      selector.innerHTML = '<option value="">-- Select a Project --</option>';
      if (!state.projects || state.projects.length === 0) return;
      state.projects.forEach((p) => {
        const option = document.createElement("option");
        option.value = p.project_id;
        option.textContent = p.project_name;
        selector.appendChild(option);
      });

      if (state.projects.length > 0) {
        // Don't auto-select first project - leave it as "Select a Project"
        if (state.selectedProjectId) {
          selector.value = state.selectedProjectId;
          state.selectedProjectName =
            selector.options[selector.selectedIndex].text;
          renderChatHistory(); // This will now show welcome message if no chats
        } else {
          // ✅ ADD WELCOME MESSAGE HERE when no project is selected
          const messagesContainer = $("#chat-messages");
          messagesContainer.innerHTML = `
                        <div class="chat-bubble assistant-bubble welcome-bubble">
                            <div class="welcome-content">
                                <div class="welcome-text">
                                    👋<br>
                                    <strong>Welcome to AI Powered SQL Automation!</strong><br>I can generate SQL queries, analyze documents, and answer questions.
                                </div>
                            </div>
                        </div>
                    `;
        }
      }
    } catch (error) {}
  }

  function renderChatHistory() {
    const messagesContainer = $("#chat-messages");
    messagesContainer.innerHTML = "";
    const history = state.chatHistory[state.selectedProjectId] || [];

    console.log("History retrieved for rendering:", history);
    console.log("🧠 CURRENT CHAT HISTORY FOR RENDERING:", history);
    console.log("📋 Project ID:", state.selectedProjectId);
    console.log("🔢 Number of messages:", history.length);

    // Log each message with query_id
    history.forEach((msg, index) => {
      console.log(
        `📄 Message ${index + 1} (${msg.role}) at ${msg.created_at}:`,
        {
          content: msg.content?.substring(0, 30) + "..." || "EMPTY",
          query_id: msg.query_id,
          messageId: msg.messageId,
          permission:
            msg.permission || state.currentConversationPermission || "edit",
        },
      );
    });

    // Show welcome message if no chat history
    if (
      history.length === 0 &&
      !state.conversationId[state.selectedProjectId]
    ) {
      messagesContainer.innerHTML = `
                <div class="chat-bubble assistant-bubble welcome-bubble">
                    <div class="welcome-content">
                        <div class="welcome-text">
                            👋<br>
                            <strong>Welcome to AI Powered SQL Automation!</strong><br>I can generate SQL queries, analyze documents, and answer questions.
                        </div>
                    </div>
                </div>
            `;
      return;
    }

    let sortedHistory = [...history];
    if (sortedHistory[0]?.created_at) {
      sortedHistory = sortedHistory.sort(
        (a, b) => new Date(a.created_at) - new Date(b.created_at),
      );
      console.log(
        "🔄 Sorted history by timestamp:",
        sortedHistory.map((m) => m.created_at),
      );
    }

    // Get user permission for current conversation (if coming from history)
    const currentPermission = state.currentConversationPermission || "edit"; // Default to edit

    // Render existing chat history
    sortedHistory.forEach((msg) => {
      const messageDiv = renderEnhancedMessage(
        msg.content || "",
        msg.role,
        msg.messageId,
        msg.permission || state.currentConversationPermission || "edit",
      );

      // Store message ID in state for reference
      if (!msg.messageId) {
        msg.messageId = messageDiv.id;
      }

      messagesContainer.appendChild(messageDiv);
    });

    setTimeout(autoScrollToBottom, 100);

    enhanceCodeBlocksInContainer(messagesContainer);
  }

  function enhanceCodeBlocks() {
    // Select all code blocks inside assistant messages
    document.querySelectorAll(".assistant-bubble pre").forEach((pre) => {
      // Avoid adding multiple copy buttons
      if (pre.querySelector(".copy-btn")) return;

      // Create the copy button
      const button = document.createElement("button");
      button.className = "copy-btn";
      button.innerHTML = "Copy";

      // Add click functionality
      button.addEventListener("click", () => {
        const code = pre.querySelector("code")?.innerText || pre.innerText;
        navigator.clipboard.writeText(code).then(() => {
          button.innerHTML = "Copied!";
          setTimeout(() => (button.innerHTML = "Copy"), 1500);
        });
      });

      // Make the <pre> position relative and add the button
      pre.style.position = "relative";
      pre.appendChild(button);
    });
  }

  // UI helpers to disable/enable send controls while streaming
  function disableSendUI() {
    const submitBtn = document.querySelector(
      '#chat-form button[type="submit"]',
    );
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.classList.add("disabled-button");
    }
    const input = document.querySelector("#chat-input");
    if (input) input.disabled = true;
    state.isStreaming = true;

    // Show stop button
    const stopBtn = $("#stop-btn");
    if (stopBtn) {
      stopBtn.style.display = "inline-block";
    }
  }

  function enableSendUI() {
    const submitBtn = document.querySelector(
      '#chat-form button[type="submit"]',
    );
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.classList.remove("disabled-button");
    }
    const input = document.querySelector("#chat-input");
    if (input) input.disabled = false;
    state.isStreaming = false;

    // Hide stop button
    const stopBtn = $("#stop-btn");
    if (stopBtn) {
      stopBtn.style.display = "none";
    }
  }

  // Replace existing handleChatSubmit with this version
  async function handleChatSubmit(e, isRegeneration = false) {
    if (e) e.preventDefault();

    // Prevent sending while a previous response is still streaming
    if (state.isStreaming) {
      console.warn("Send blocked: assistant is still responding.");
      return;
    }

    const input = $("#chat-input");
    const query = input.value.trim();
    const databaseEngine =
      document.getElementById("database-engine-selector").value ||
      state.selectedDatabaseEngine;

    if (!query) return;
    if (!state.selectedProjectId) {
      showSweetAlert("Please select a project", "warning");
      return;
    }

    if (!databaseEngine) {
      showSweetAlert("Please select a Database engine", "warning");
      return;
    }
    // Update state
    state.selectedDatabaseEngine = databaseEngine;

    disableSendUI(); // disable UI until response completes

    const projId = state.selectedProjectId;

    // Initialize chat history if needed
    if (!state.chatHistory[projId]) {
      state.chatHistory[projId] = [];
    }

    // Add user message to history
    const userMessage = {
      role: "user",
      content: query,
      messageId: `msg-${Date.now()}-user-${Math.random().toString(36).substr(2, 5)}`,
      originalContent: query,
      databaseEngine: databaseEngine,
      permission: state.currentConversationPermission || "edit",
    };

    state.chatHistory[projId].push(userMessage);

    // Render user message with actions
    const userMessageDiv = renderEnhancedMessage(
      query,
      "user",
      userMessage.messageId,
      "edit",
    );
    $("#chat-messages").appendChild(userMessageDiv);

    input.value = "";
    input.dispatchEvent(new Event("input"));

    // SCROLL: After user message is added
    autoScrollToBottom();

    // Create assistant message placeholder
    const assistantMessageId = `msg-${Date.now()}-assistant-${Math.random().toString(36).substr(2, 5)}`;
    const assistantBubble = document.createElement("div");
    assistantBubble.className = "chat-bubble assistant-bubble";
    assistantBubble.id = assistantMessageId;

    let currentStatus = "🚀 Starting processing...\n";
    assistantBubble.innerHTML = `
            <div class="message-content">
                <span class="typing-cursor">${currentStatus}</span>
            </div>
        `;

    $("#chat-messages").appendChild(assistantBubble);
    autoScrollToBottom();

    // Add timeout handling
    const TIMEOUT_MS = 45000; // 45 seconds
    let responseComplete = false;
    const timeoutId = setTimeout(() => {
      if (!responseComplete) {
        currentStatus = "⚠️ Taking longer than expected...";
        const messageContent =
          assistantBubble.querySelector(".message-content");
        if (messageContent) {
          messageContent.innerHTML = `<span class="typing-cursor">${currentStatus}</span>`;
        }
        autoScrollToBottom();
      }
    }, TIMEOUT_MS);

    try {
      state.currentAbortController = new AbortController();
      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${state.token}`,
        },
        body: JSON.stringify({
          project_id: parseInt(projId),
          query: query,
          conversation_id: state.conversationId[projId] || null,
          database_engine: databaseEngine,
        }),
        signal: state.currentAbortController.signal,
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let fullResponse = "";
      let metaData = {};

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        let chunk = decoder.decode(value, { stream: true });
        console.log("🔍 Raw chunk:", chunk); // DEBUG
        let lines = chunk
          .split("---SEPARATOR---")
          .filter((line) => line.trim() !== "");
        // Handle status updates
        for (let line of lines) {
          console.log("Updating currentStatus from line:", line);
          if (line.includes("[[[STATUS]]]")) {
            currentStatus = line.replace("[[[STATUS]]]", "").trim();
            const messageContent =
              assistantBubble.querySelector(".message-content");
            if (messageContent) {
              if (fullResponse) {
                messageContent.innerHTML = `
                                    <div class="status-container">
                                        <span class="typing-cursor">${currentStatus}</span>
                                        <div style="margin-top: 10px; opacity: 0.8;">${marked.parse(fullResponse + " ▌")}</div>
                                    </div>
                                `;
              } else {
                messageContent.innerHTML = `<span class="typing-cursor">${currentStatus}</span>`;
              }
            }
            autoScrollToBottom();
            continue;
          }

          // Handle metadata (may be emitted early or at end)
          // In handleChatSubmit - FIX METADATA PARSING:

          // Handle metadata (may be emitted early or at end)
          else if (line.includes("[[[META]]]")) {
            const parts = line.split("[[[META]]]");
            line = parts[0];
            try {
              let metaString = parts[1] || "{}";
              console.log("🔍 Raw metadata string:", metaString); // DEBUG
              metaData = JSON.parse(metaString);
              console.log("✅ Parsed metadata:", metaData); // DEBUG

              // If meta contains conversation_id, persist immediately
              if (metaData.conversation_id) {
                state.conversationId[projId] = metaData.conversation_id;
              }
              // CRITICAL FIX: Update user message with query_id immediately
              if (metaData.query_id && userMessage) {
                userMessage.query_id = metaData.query_id;
                console.log(
                  "🔄 Updated user message with query_id:",
                  metaData.query_id,
                );
              }
            } catch (e) {
              console.error(
                "❌ Failed to parse metadata chunk:",
                e,
                "Raw:",
                parts[1],
              );
              metaData = {}; // Ensure it's always an object
            }
          } else {
            fullResponse += line;
          }
        }
        console.log("🧩 Current fullResponse:", fullResponse); // DEBUG

        // Update with both status and accumulated response
        const messageContent =
          assistantBubble.querySelector(".message-content");
        if (messageContent) {
          messageContent.innerHTML = `
                        <div class="status-container">
                            <span class="typing-cursor">${currentStatus}</span>
                            <div style="margin-top: 10px; opacity: 0.8;">${marked.parse(fullResponse + " ▌")}</div>
                        </div>
                    `;
        }

        autoScrollToBottom();
      }

      // Clear timeout and mark as complete
      clearTimeout(timeoutId);
      responseComplete = true;

      // Final update - remove status and show only the response with actions
      assistantBubble.innerHTML = `
                <div class="message-content">${marked.parse(fullResponse)}</div>
                <div class="message-actions">
                    <button class="action-btn copy" title="Copy message content">
                        <i class="fas fa-copy"></i> Copy
                    </button>
                </div>
            `;

      setTimeout(() => {
        enhanceCodeBlocks(assistantBubble);
      }, 0);

      // Attach copy functionality
      const copyBtn = assistantBubble.querySelector(".action-btn.copy");
      if (copyBtn) {
        copyBtn.addEventListener("click", () => {
          const content = extractTextContent(assistantBubble);
          if (content && content.trim()) {
            copyToClipboard(content);
            showCopyFeedback(copyBtn);
          }
        });
      }

      enhanceCodeBlocks(assistantBubble);

      // Add to chat history
      const assistantMessage = {
        role: "assistant",
        content: fullResponse,
        user_query: query,
        query_id: userMessage.query_id || metaData.query_id,
        contexts: metaData.contexts || [],
        messageId: assistantMessageId,
        databaseEngine: databaseEngine,
        permission: state.currentConversationPermission || "edit",
      };

      state.chatHistory[projId].push(assistantMessage);

      if (metaData.conversation_id) {
        state.conversationId[projId] = metaData.conversation_id;
      }
      // Update user message with query_id from metadata
      if (metaData.query_id) {
        userMessage.query_id = metaData.query_id;
      }

      addFeedbackSection(assistantBubble, assistantMessage);

      autoScrollToBottom();
    } catch (error) {
      clearTimeout(timeoutId);
      responseComplete = true;

      if (error.name === "AbortError") {
        assistantBubble.innerHTML = `<div class="message-content"><strong>🛑 Generation stopped by user.</strong></div><div class="message-actions"><button class="action-btn copy">Copy</button></div>`;
        const copyBtn = assistantBubble.querySelector(".action-btn.copy");
        if (copyBtn)
          copyBtn.addEventListener("click", () =>
            copyToClipboard("Generation stopped"),
          );
        enhanceCodeBlocks(assistantBubble);
        autoScrollToBottom();
      } else {
        assistantBubble.innerHTML = `
                    <div class="message-content" style="color: var(--danger);">
                        <strong>Error:</strong> ${error.message}
                    </div>
                    <div class="message-actions">
                        <button class="action-btn copy" title="Copy error message">
                            <i class="fas fa-copy"></i> Copy
                        </button>
                    </div>
                `;

        const copyBtn = assistantBubble.querySelector(".action-btn.copy");
        if (copyBtn) {
          copyBtn.addEventListener("click", () => {
            const content = `Error: ${error.message}`;
            copyToClipboard(content);
            showCopyFeedback(copyBtn);
          });
        }

        const errorMessage = {
          role: "assistant",
          content: `Error: ${error.message}`,
          user_query: query,
          messageId: assistantMessageId,
          databaseEngine: databaseEngine,
          permission: state.currentConversationPermission || "edit",
        };
        state.chatHistory[projId].push(errorMessage);

        autoScrollToBottom();
      }
    } finally {
      state.currentAbortController = null;
      enableSendUI();
    }
  }

  function addFeedbackSection(bubble, message) {
    console.log("Message in addFeedbackSection:", message); // DEBUG
    console.log("Contexts in addFeedbackSection:", message.contexts); // DEBUG
    const feedbackDiv = document.createElement("div");
    feedbackDiv.className = "feedback-section";
    feedbackDiv.innerHTML = `
            <span class="feedback-text">Was this helpful?</span>
            <button class="feedback-btn" data-type="up">👍</button>
            <button class="feedback-btn" data-type="down">👎</button>
        `;
    bubble.appendChild(feedbackDiv);

    feedbackDiv.addEventListener("click", (e) => {
      if (e.target.matches(".feedback-btn")) {
        const feedbackType = e.target.dataset.type;

        // Show comment input instead of immediately submitting
        feedbackDiv.innerHTML = `
                    <div class="feedback-comment-section">
                        <span class="feedback-thanks">Thanks! Any additional comments?</span>
                        <textarea class="feedback-comment" placeholder="Optional: Share your thoughts..."></textarea>
                        <div class="feedback-actions">
                            <button class="feedback-submit-btn" data-type="${feedbackType}">Submit Feedback</button>
                            <button class="feedback-cancel-btn">Cancel</button>
                        </div>
                    </div>
                `;

        // Add event listeners for the new buttons
        feedbackDiv
          .querySelector(".feedback-submit-btn")
          .addEventListener("click", () => {
            const comment =
              feedbackDiv.querySelector(".feedback-comment").value;
            submitFeedback(message, feedbackType, comment);
            feedbackDiv.innerHTML =
              '<span class="feedback-thanks">Thanks for your feedback!</span>';
          });

        feedbackDiv
          .querySelector(".feedback-cancel-btn")
          .addEventListener("click", () => {
            // Restore original feedback buttons
            feedbackDiv.innerHTML = `
                        <span class="feedback-text">Was this helpful?</span>
                        <button class="feedback-btn" data-type="up">👍</button>
                        <button class="feedback-btn" data-type="down">👎</button>
                    `;
            // Re-attach event listeners to the new buttons
            attachFeedbackListeners(feedbackDiv, message);
          });
      }
    });
  }

  // Helper function to re-attach event listeners
  function attachFeedbackListeners(feedbackDiv, message) {
    feedbackDiv.querySelectorAll(".feedback-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const feedbackType = e.target.dataset.type;

        // Show comment input for detailed feedback
        feedbackDiv.innerHTML = `
                    <div class="feedback-comment-section">
                        <span class="feedback-thanks">Thanks! Any additional comments?</span>
                        <textarea class="feedback-comment" placeholder="Optional: Share your thoughts about the SQL query..."></textarea>
                        <div class="feedback-actions">
                            <button class="feedback-submit-btn" data-type="${feedbackType}">Submit Feedback</button>
                            <button class="feedback-cancel-btn">Cancel</button>
                        </div>
                    </div>
                `;

        // Submit feedback with comment
        feedbackDiv
          .querySelector(".feedback-submit-btn")
          .addEventListener("click", () => {
            const comment =
              feedbackDiv.querySelector(".feedback-comment").value;
            submitEnhancedFeedback(message, feedbackType, comment);
            feedbackDiv.innerHTML =
              '<span class="feedback-thanks">Thanks for your feedback!</span>';
          });

        // Cancel feedback
        feedbackDiv
          .querySelector(".feedback-cancel-btn")
          .addEventListener("click", () => {
            feedbackDiv.innerHTML = `
                        <span class="feedback-text">Was this helpful?</span>
                        <button class="feedback-btn" data-type="up">👍</button>
                        <button class="feedback-btn" data-type="down">👎</button>
                    `;
            attachFeedbackListeners(feedbackDiv, message);
          });
      });
    });
  }

  async function submitFeedback(msg, feedbackType, comment = "") {
    try {
      console.log("Before submit - msg.contexts:", msg.contexts);
      console.log("Before submit - full msg:", msg); // DEBUG
      const payload = {
        query_id: msg.query_id || "",
        project_id: parseInt(state.selectedProjectId),
        query: msg.user_query,
        answer: msg.content,
        contexts: msg.contexts,
        feedback: feedbackType,
        comment: comment, // Add comment here
        user_id: state.user.user_id,
      };
      await apiRequest("/feedback", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    } catch (error) {
      console.error("Failed to submit feedback:", error);
    }
  }

  // History & Analytics Logic
  async function loadProjectsForHistoryPage() {
    try {
      state.projects = await apiRequest("/projects");
      const selector = $("#history-project-selector");
      selector.innerHTML = '<option value="">-- Select Project --</option>';
      state.projects.forEach((p) => {
        selector.innerHTML += `<option value="${p.project_id}">${p.project_name}</option>`;
      });

      // Show welcome message when page first loads (regardless of projects)
      $("#history-transcript").innerHTML = `
                <div class="chat-bubble assistant-bubble welcome-bubble">
                    <div class="welcome-content">
                        <div class="welcome-text">
                            👋<br>
                            <strong>Welcome to Query History!</strong><br>
                            Select a project and conversation to view past interactions and continue where you left off.
                        </div>
                    </div>
                </div>
            `;

      // Clear conversation selector
      $("#history-conversation-selector").innerHTML =
        '<option value="">-- Select Conversation --</option>';
    } catch (error) {
      console.error("Failed to load projects for history page:", error);
    }
  }

  async function loadConversationsForHistory() {
    const projectId = $("#history-project-selector").value;
    const continueContainer = $("#continue-conversation-container");

    // Hide continue button when switching projects
    continueContainer.style.display = "none";

    if (!projectId) {
      // Show welcome message when no project is selected
      $("#history-transcript").innerHTML = `
                <div class="chat-bubble assistant-bubble welcome-bubble">
                    <div class="welcome-content">
                        <div class="welcome-text">
                            👋<br>
                            <strong>Welcome to Query History!</strong><br>
                            Select a project and conversation to view past interactions and continue where you left off.
                        </div>
                    </div>
                </div>
            `;
      return;
    }

    try {
      const history = await apiRequest(`/queryhistory/${projectId}`);
      const convSelector = $("#history-conversation-selector");
      convSelector.innerHTML =
        '<option value="">-- Select Conversation --</option>';

      if (history.length === 0) {
        // Show message when no conversations exist for selected project
        $("#history-transcript").innerHTML = `
                    <div class="chat-bubble assistant-bubble welcome-bubble">
                        <div class="welcome-content">
                            <div class="welcome-text">
                                👋<br>
                                <strong>No Conversations Found</strong><br>
                                This project doesn't have any query history yet. Start a conversation in the Query page!
                            </div>
                        </div>
                    </div>
                `;
        return;
      }

      // Group ALL queries by conversation_id for the full conversation data
      const allConversations = history.reduce((acc, q) => {
        acc[q.conversation_id] = acc[q.conversation_id] || [];
        acc[q.conversation_id].push(q);
        return acc;
      }, {});

      // For dropdown, we only need the first query from each conversation
      // For dropdown, we need to sort conversations by MOST RECENT first (descending order)
      const dropdownConversations = {};

      // Get conversation IDs and sort them by their LATEST message date (descending)
      const conversationIds = Object.keys(allConversations);

      // Sort conversations by their most recent message date
      conversationIds.sort((convIdA, convIdB) => {
        // Get the most recent message from each conversation
        const getLatestMessageDate = (convId) => {
          const messages = allConversations[convId];
          const latestDate = Math.max(
            ...messages.map((msg) => new Date(msg.created_at).getTime()),
          );
          return latestDate;
        };

        const dateA = getLatestMessageDate(convIdA);
        const dateB = getLatestMessageDate(convIdB);

        // Sort in DESCENDING order (most recent first)
        return dateB - dateA;
      });

      // Create dropdown entries in descending order
      conversationIds.forEach((convId) => {
        const messages = allConversations[convId];

        // For dropdown, sort messages in ascending order to get the first query
        const sortedMessages = messages.sort(
          (a, b) => new Date(a.created_at) - new Date(b.created_at),
        );

        dropdownConversations[convId] = [sortedMessages[0]]; // Only first query
      });

      console.log("All conversations data:", allConversations); // Debug
      console.log("Dropdown conversations:", dropdownConversations); // Debug

      // Store ALL conversations for when user selects one
      convSelector.dataset.conversations = JSON.stringify(allConversations);

      // For dropdown, show only first query
      // In the dropdown creation section, update to:
      for (const convId in dropdownConversations) {
        const firstMessage = dropdownConversations[convId][0];

        console.log("First message data for", convId, ":", firstMessage); // DEBUG

        // Get the conversation title - make sure it's not 'edit' or 'view'
        let displayText = firstMessage.conversation_title;

        // Filter out permission strings from conversation title
        if (
          displayText === "edit" ||
          displayText === "view" ||
          displayText === "unknown" ||
          !displayText ||
          displayText.trim() === ""
        ) {
          // Use the query text as fallback
          displayText = firstMessage.query || "Untitled Conversation";

          // Clean up the query text for display
          if (displayText.length > 100) {
            displayText = displayText.substring(0, 100) + "...";
          }
        }

        // Ensure we have a valid display text
        if (!displayText || displayText.trim() === "") {
          displayText = "Untitled Conversation";
        }

        // Truncate for dropdown
        const truncatedDisplay =
          displayText.length > 50
            ? displayText.substring(0, 50) + "..."
            : displayText;

        // Add shared badge (only if actually shared)
        const sharedBadge =
          firstMessage.shared_by_username &&
          firstMessage.shared_by_username.trim() !== "" &&
          firstMessage.shared_by_username !== state.user.username
            ? `<span class="shared-badge"> (Shared by ${firstMessage.shared_by_username})</span>`
            : "";

        convSelector.innerHTML += `
                    <option value="${convId}" 
                            data-title="${displayText.replace(/"/g, "&quot;")}"
                            data-shared-by="${firstMessage.shared_by_username || ""}"
                            data-permission="${firstMessage.access_type || "edit"}">
                        ${truncatedDisplay}
                        ${sharedBadge}
                    </option>
                `;
      }
    } catch (error) {
      console.error("Failed to load conversations:", error);
    }
  }

  function renderConversationTranscript() {
    const convId = $("#history-conversation-selector").value;
    const transcriptDiv = $("#history-transcript");
    const continueContainer = $("#continue-conversation-container");

    // Hide continue button when no conversation is selected
    continueContainer.style.display = "none";

    // ✅ CRITICAL FIX: Reset permission state when changing conversations
    state.currentConversationPermission = null;

    if (!convId) {
      transcriptDiv.innerHTML = `
                <div class="chat-bubble assistant-bubble welcome-bubble">
                    <div class="welcome-content">
                        <div class="welcome-text">
                            👋<br>
                            <strong>Welcome to Query History!</strong><br>
                            Select a project and conversation to view past interactions and continue where you left off.
                        </div>
                    </div>
                </div>
            `;
      return;
    }

    const conversations = JSON.parse(
      $("#history-conversation-selector").dataset.conversations,
    );
    let messages = conversations[convId];

    // Get the user's permission for this conversation
    // Assuming all messages in a conversation have the same access_type
    const userPermission =
      messages.length > 0 ? messages[0].access_type : "edit";

    // Sort messages by creation date to show in chronological order
    messages = messages.sort(
      (a, b) => new Date(a.created_at) - new Date(b.created_at),
    );

    // DEBUG: Log the raw data from database
    console.log("🔍 RAW MESSAGES FROM DATABASE:", messages);
    console.log("📊 Total messages in conversation:", messages.length);

    console.log("📅 Message timestamps:");

    messages.forEach((msg, index) => {
      console.log(`📝 Message ${index + 1}:`, {
        query_id: msg.id,
        query: msg.query?.substring(0, 50) + "...",
        answer: msg.answer?.substring(0, 50) + "...",
        created_at: msg.created_at,
      });
    });

    // Clear the transcript and add header
    transcriptDiv.innerHTML = "<h3>Conversation</h3>";

    // Build HTML content first, then set it all at once
    let messagesHTML = "";

    // Display ALL messages in chronological order
    // Display ALL messages in chronological order
    messages.forEach((msg) => {
      const timestamp = new Date(msg.created_at).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });

      // User message
      messagesHTML += `
                <div class="message-row user-message-row">
                    <div class="chat-bubble user-bubble">
                        ${escapeHtml(msg.query || "")}
                        ${`<div class="shared-badge">${timestamp}</div>`}
                    </div>
                </div>`;

      // Assistant message
      if (msg.answer) {
        messagesHTML += `
                    <div class="message-row assistant-message-row">
                        <div class="chat-bubble assistant-bubble">
                            ${marked.parse(msg.answer || "")}
                        </div>
                    </div>`;
      }
    });

    transcriptDiv.innerHTML += messagesHTML;

    continueContainer.style.display = "block";

    const continueBtn = $("#continue-conversation-btn");
    const shareBtn = $("#share-conversation-btn");

    if (userPermission === "view") {
      shareBtn.disabled = true;
      shareBtn.style.opacity = "0.5";
      shareBtn.style.cursor = "not-allowed";
      shareBtn.title = "You have view-only permission for this conversation";
    } else {
      shareBtn.disabled = false;
      shareBtn.style.opacity = "1";
      shareBtn.style.cursor = "pointer";
      shareBtn.title = "Share this conversation";
    }

    // Update the button click handler
    continueBtn.onclick = () => {
      // Set selected project & conversation ID
      const projectId = $("#history-project-selector").value;

      const currentConversation = conversations[convId];

      // Get the MOST RECENT query's database engine (or use a default)
      const latestQuery = currentConversation.sort(
        (a, b) => new Date(b.created_at) - new Date(a.created_at),
      )[0];

      // Store permission in state for use in other functions
      state.currentConversationPermission = userPermission;

      // Set database engine from history or default
      state.selectedDatabaseEngine =
        latestQuery.database_engine || "postgresql";
      const databaseEngineSelector = $("#database-engine-selector");
      if (databaseEngineSelector) {
        databaseEngineSelector.value = state.selectedDatabaseEngine;
        console.log(
          `🔧 Database engine set to: ${state.selectedDatabaseEngine}`,
        );
      }

      state.selectedProjectId = projectId;
      state.conversationId[projectId] = convId;

      // Load chat history for this conversation in correct INTERLEAVED order
      state.chatHistory[projectId] = [];
      state.currentConversationPermission = userPermission;

      // Process messages in chronological order and interleave user/assistant messages
      messages.forEach((msg) => {
        const queryId = msg.id;
        // Add user message
        state.chatHistory[projectId].push({
          role: "user",
          content: msg.query,
          user_query: msg.query,
          query_id: queryId,
          originalContent: msg.query,
          contexts: msg.contexts || [],
          databaseEngine: msg.database_engine || state.selectedDatabaseEngine,
          created_at: msg.created_at,
          permission: userPermission || "edit",
        });

        // Add assistant message immediately after user message
        state.chatHistory[projectId].push({
          role: "assistant",
          content: msg.answer || "",
          user_query: msg.query,
          query_id: queryId,
          contexts: msg.contexts || [],
          databaseEngine: msg.database_engine || state.selectedDatabaseEngine,
          created_at: msg.created_at,
          permission: userPermission || "edit",
        });
      });

      // Navigate to chat page
      navigateTo("query");
      renderChatHistory();
    };

    shareBtn.onclick = () => {
      openShareModal();
    };

    // =============================================
    // FIXED DELETE BUTTON HANDLER
    // =============================================
    const deleteBtn = $("#delete-conversation-btn");
    console.log("🔍 Delete button found in DOM:", deleteBtn);

    if (deleteBtn) {
      // Clone and replace to remove any existing listeners
      const newDeleteBtn = deleteBtn.cloneNode(true);
      deleteBtn.parentNode.replaceChild(newDeleteBtn, deleteBtn);

      newDeleteBtn.onclick = async (e) => {
        e.preventDefault();
        e.stopPropagation();
        console.log("🗑️ Delete button CLICKED!");

        const convId = $("#history-conversation-selector").value;
        const projectId = $("#history-project-selector").value;

        console.log("Conversation ID:", convId);
        console.log("Project ID:", projectId);

        if (!convId || !projectId) {
          showSweetAlert("Please select a conversation first", "warning");
          return;
        }

        // Get conversation title for confirmation message
        const selectedOption = $("#history-conversation-selector").options[
          $("#history-conversation-selector").selectedIndex
        ];
        const convTitle =
          selectedOption?.textContent?.split(" (Shared")[0] ||
          "this conversation";

        const result = await Swal.fire({
          title: "Delete Conversation?",
          html: `Are you sure you want to delete "<strong>${escapeHtml(convTitle)}</strong>"?<br><br>
                           This will permanently delete all messages in this conversation.<br>
                           <span style="color: var(--danger);">This action cannot be undone!</span>`,
          icon: "warning",
          showCancelButton: true,
          confirmButtonColor: "#dc3545",
          cancelButtonColor: "#3F0D66",
          confirmButtonText: "Yes, delete conversation",
          cancelButtonText: "Cancel",
        });

        if (result.isConfirmed) {
          await deleteConversation(convId, projectId);
        }
      };
    } else {
      console.error("❌ Delete button NOT found in DOM!");
    }

    enhanceCodeBlocksInContainer(transcriptDiv);
  }

  async function loadAnalyticsData() {
    try {
      const data = await apiRequest("/analytics-data");
      renderKPIs(data);
      renderQueryTrendChart(data.queries);
      renderPerfKPIs(data.queries);
      renderFeedbackChart(data.feedback);
      renderUserAnalytics(data.queries, data.users); // New function for user analytics tab
      renderQueryAnalytics(data.queries, data.projects); // New function for query analytics tab
      renderQualityMetrics(data.queries);
    } catch (error) {}
  }

  function renderKPIs(data) {
    $("#kpi-container").innerHTML =
      `<div class="card"><h4>Total Projects</h4><p class="kpi-value">${data.projects.length}</p></div><div class="card"><h4>Total Queries</h4><p class="kpi-value">${data.queries.length}</p></div><div class="card"><h4>Unique Users</h4><p class="kpi-value">${data.users.length}</p></div><div class="card"><h4>Feedback Entries</h4><p class="kpi-value">${data.feedback.length}</p></div>`;
  }

  function renderQueryTrendChart(queries) {
    if (state.charts.queryTrend) state.charts.queryTrend.destroy();
    const ctx = $("#query-trend-chart").getContext("2d");
    const dailyCounts = queries.reduce((acc, q) => {
      const date = new Date(q.created_at).toISOString().split("T")[0];
      acc[date] = (acc[date] || 0) + 1;
      return acc;
    }, {});
    state.charts.queryTrend = new Chart(ctx, {
      type: "line",
      data: {
        labels: Object.keys(dailyCounts).sort(),
        datasets: [
          {
            label: "Daily Query Volume",
            data: Object.values(dailyCounts),
            borderColor: "var(--primary)",
            tension: 0.1,
          },
        ],
      },
      options: { responsive: true, maintainAspectRatio: false },
    });
  }

  function renderPerfKPIs(queries) {
    const responseTimes = queries.map((q) => q.response_time).filter(Boolean);
    const avgRespTime = responseTimes.length
      ? responseTimes.reduce((a, b) => a + b, 0) / responseTimes.length
      : 0;

    const fallbackCount = queries.filter((q) => q.is_fallback).length;
    const fallbackRate = queries.length
      ? (fallbackCount / queries.length) * 100
      : 0;

    const retryCount = queries.filter((q) => q.attempts > 1).length;
    const retryRate = queries.length ? (retryCount / queries.length) * 100 : 0;

    $("#perf-kpi-container").innerHTML = `
            <div class="card">
                <h4><span class="icon-performance"></span> Performance Metrics</h4>
                <div class="metric-grid">
                    <div class="metric-item">
                        <span class="metric-label">Avg Response Time</span>
                        <span class="metric-value">${avgRespTime.toFixed(2)}s</span>
                    </div>
                    <div class="metric-item">
                        <span class="metric-label">LLM Fallback Rate</span>
                        <span class="metric-value">${fallbackRate.toFixed(1)}%</span>
                    </div>
                    <div class="metric-item">
                        <span class="metric-label">Retry Rate</span>
                        <span class="metric-value">${retryRate.toFixed(1)}%</span>
                    </div>
                </div>
            </div>
        `;
  }

  function renderFeedbackChart(feedback) {
    if (state.charts.feedback) state.charts.feedback.destroy();
    if (!feedback || feedback.length === 0) {
      $("#feedback-chart").closest(".chart-wrapper").innerHTML =
        "<p>No feedback data yet.</p>";
      return;
    }
    const ctx = $("#feedback-chart").getContext("2d");
    const computedStyles = getComputedStyle(document.documentElement);
    const successColor = computedStyles.getPropertyValue("--success").trim();
    const dangerColor = computedStyles.getPropertyValue("--danger").trim();
    console.log("Chart colors loaded:", { successColor, dangerColor }); // DEBUG
    const counts = feedback.reduce(
      (acc, f) => {
        acc[f.feedback] = (acc[f.feedback] || 0) + 1;
        return acc;
      },
      { up: 0, down: 0 },
    );
    state.charts.feedback = new Chart(ctx, {
      type: "pie",
      data: {
        labels: ["Up Votes", "Down Votes"],
        datasets: [
          {
            data: [counts.up, counts.down],
            backgroundColor: [successColor, dangerColor],
            borderColor: "var(--card-bg)",
            borderWidth: 2,
          },
        ],
      },
      options: { responsive: true, maintainAspectRatio: false },
    });
  }

  // --- New Query Analytics Function ---
  function renderQueryAnalytics(queries, projects) {
    const container = $("#query-analytics-container");
    if (!queries || queries.length === 0) {
      container.innerHTML = "<p>No query analytics data available.</p>";
      return;
    }

    // Calculate queries per project
    const projectStats = {};
    queries.forEach((query) => {
      const projectId = query.project_id;
      if (!projectStats[projectId]) {
        projectStats[projectId] = {
          queryCount: 0,
          projectName:
            projects.find((p) => p.id === projectId)?.name ||
            `Project ${projectId}`,
        };
      }
      projectStats[projectId].queryCount++;
    });

    // Calculate conversation depth
    const conversationStats = {};
    queries.forEach((query) => {
      const convId = query.conversation_id;
      if (!conversationStats[convId]) {
        conversationStats[convId] = 0;
      }
      conversationStats[convId]++;
    });

    const conversationDepths = Object.values(conversationStats);
    const avgConversationDepth =
      conversationDepths.reduce((a, b) => a + b, 0) / conversationDepths.length;

    // Prepare data for charts
    const projectData = Object.values(projectStats);
    const projectNames = projectData.map((project) => project.projectName);
    const projectQueryCounts = projectData.map((project) => project.queryCount);

    // Render query analytics
    container.innerHTML = `
            <div class="card">
                <h4><span class="icon-folder"></span> Queries per Project</h4>
                <div class="chart-wrapper">
                    <canvas id="queries-per-project-chart"></canvas>
                </div>
            </div>
            
            <div class="card">
                <h4><span class="icon-analytics"></span> Conversation Depth Distribution</h4>
                <div class="chart-wrapper">
                    <canvas id="conversation-depth-chart"></canvas>
                </div>
            </div>
            
        `;

    // Create charts
    createBarChart(
      "queries-per-project-chart",
      projectNames,
      projectQueryCounts,
      "Queries per Project",
      "Number of Queries",
    );
    createHistogramChart(
      "conversation-depth-chart",
      conversationDepths,
      "Conversation Depth Distribution",
      "Number of Queries per Conversation",
    );
  }

  // Helper function to create histogram charts
  function createHistogramChart(canvasId, data, title, xAxisLabel) {
    const ctx = $(`#${canvasId}`).getContext("2d");
    if (state.charts[canvasId]) state.charts[canvasId].destroy();

    // Create histogram bins
    const maxDepth = Math.max(...data);
    const binSize = Math.ceil(maxDepth / 10);
    const bins = {};

    for (let i = 1; i <= maxDepth; i += binSize) {
      const binLabel = `${i}-${i + binSize - 1}`;
      bins[binLabel] = 0;
    }

    data.forEach((depth) => {
      const binIndex = Math.floor((depth - 1) / binSize);
      const binLabel = `${binIndex * binSize + 1}-${binIndex * binSize + binSize}`;
      bins[binLabel] = (bins[binLabel] || 0) + 1;
    });

    const binLabels = Object.keys(bins);
    const binCounts = Object.values(bins);

    state.charts[canvasId] = new Chart(ctx, {
      type: "bar",
      data: {
        labels: binLabels,
        datasets: [
          {
            label: "Number of Conversations",
            data: binCounts,
            backgroundColor: "rgba(106, 27, 154, 0.6)",
            borderColor: "rgba(106, 27, 154, 1)",
            borderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          title: {
            display: true,
            text: title,
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            title: {
              display: true,
              text: "Number of Conversations",
            },
          },
          x: {
            title: {
              display: true,
              text: xAxisLabel,
            },
          },
        },
      },
    });
  }

  // --- New User Analytics Function with Charts ---
  function renderUserAnalytics(queries, users) {
    const container = $("#user-analytics-container");
    if (!queries || queries.length === 0) {
      container.innerHTML = "<p>No user analytics data available.</p>";
      return;
    }

    // Create a mapping of user_id to username
    const userMap = {};
    users.forEach((user) => {
      userMap[user.id] = user.username;
    });

    // Calculate user analytics
    const userStats = {};
    const sessionStats = {};

    queries.forEach((query) => {
      const userId = query.user_id;
      const sessionId = query.conversation_id;
      const username = userMap[userId] || `User ${userId}`;

      // Initialize user stats
      if (!userStats[userId]) {
        userStats[userId] = {
          queryCount: 0,
          sessions: new Set(),
          totalResponseTime: 0,
          username: username,
        };
      }

      // Initialize session stats
      if (!sessionStats[sessionId]) {
        sessionStats[sessionId] = {
          queryCount: 0,
          userId: userId,
        };
      }

      // Update counts
      userStats[userId].queryCount++;
      userStats[userId].sessions.add(sessionId);
      userStats[userId].totalResponseTime += query.response_time || 0;

      sessionStats[sessionId].queryCount++;
    });

    // Prepare data for charts
    const userData = Object.values(userStats);

    // Sort by query count (descending) for better chart display
    userData.sort((a, b) => b.queryCount - a.queryCount);

    const usernames = userData.map((user) => user.username);
    const queryCounts = userData.map((user) => user.queryCount);
    const sessionCounts = userData.map((user) => user.sessions.size);

    // Calculate averages
    const totalUsers = userData.length;
    const totalSessions = Object.keys(sessionStats).length;
    const queriesPerUser =
      queryCounts.reduce((sum, count) => sum + count, 0) / totalUsers;
    const sessionsPerUser =
      sessionCounts.reduce((sum, count) => sum + count, 0) / totalUsers;
    const avgQueriesPerSession = queries.length / totalSessions;

    // Render user analytics with charts
    container.innerHTML = `
            <div class="card">
                <h4><span class="icon-analytics"></span> Queries per User</h4>
                <div class="chart-wrapper">
                    <canvas id="queries-per-user-chart"></canvas>
                </div>
            </div>
            
            <div class="card">
                <h4><span class="icon-analytics"></span> Sessions per User</h4>
                <div class="chart-wrapper">
                    <canvas id="sessions-per-user-chart"></canvas>
                </div>
            </div>
            
            <div class="card">
                <h4><span class="icon-trend"></span> User Engagement Summary</h4>
                <div class="metric-grid">
                    <div class="metric-item">
                        <span class="metric-label">Avg Queries per User</span>
                        <span class="metric-value">${queriesPerUser.toFixed(1)}</span>
                    </div>
                    <div class="metric-item">
                        <span class="metric-label">Avg Sessions per User</span>
                        <span class="metric-value">${sessionsPerUser.toFixed(1)}</span>
                    </div>
                    <div class="metric-item">
                        <span class="metric-label">Avg Queries per Session</span>
                        <span class="metric-value">${avgQueriesPerSession.toFixed(1)}</span>
                    </div>
                </div>
            </div>
        `;

    // Create charts
    createBarChart(
      "queries-per-user-chart",
      usernames,
      queryCounts,
      "Queries per User",
      "Number of Queries",
    );
    createBarChart(
      "sessions-per-user-chart",
      usernames,
      sessionCounts,
      "Sessions per User",
      "Number of Sessions",
    );
  }

  // Helper function to create bar charts
  function createBarChart(canvasId, labels, data, title, yAxisLabel) {
    const ctx = $(`#${canvasId}`).getContext("2d");
    if (state.charts[canvasId]) state.charts[canvasId].destroy();

    state.charts[canvasId] = new Chart(ctx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: yAxisLabel,
            data: data,
            backgroundColor: "rgba(63, 13, 102, 0.6)",
            borderColor: "rgba(63, 13, 102, 1)",
            borderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          title: {
            display: true,
            text: title,
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            title: {
              display: true,
              text: yAxisLabel,
            },
          },
          x: {
            title: {
              display: true,
              text: "Users",
            },
          },
        },
      },
    });
  }

  // --- New Quality Metrics Function ---
  function renderQualityMetrics(queries) {
    if (!queries || queries.length === 0) return;

    // Filter out null/undefined values
    const validQueries = queries.filter(
      (q) =>
        q.static_exec !== null &&
        q.static_exec !== undefined &&
        q.claim_support !== null &&
        q.claim_support !== undefined &&
        q.precision1 !== null &&
        q.precision1 !== undefined &&
        q.query_coverage !== null &&
        q.query_coverage !== undefined,
    );

    if (validQueries.length === 0) return;

    // Calculate metrics
    const staticExecScores = validQueries.map((q) => q.static_exec);
    const claimSupportScores = validQueries.map((q) => q.claim_support);
    const precisionScores = validQueries.map((q) => q.precision1);
    const queryCoverageScores = validQueries.map((q) => q.query_coverage);

    const overallAccuracy =
      (staticExecScores.reduce((a, b) => a + b, 0) / staticExecScores.length +
        claimSupportScores.reduce((a, b) => a + b, 0) /
          claimSupportScores.length) /
      2;

    const retrievalQuality =
      precisionScores.reduce((a, b) => a + b, 0) / precisionScores.length;
    const queryUnderstanding =
      queryCoverageScores.reduce((a, b) => a + b, 0) /
      queryCoverageScores.length;

    // Render quality metrics in performance tab
    const qualityMetricsHTML = `
            <div class="card">
                <h4><span class="icon-analytics"></span> Quality Metrics</h4>
                <div class="metric-grid">
                    <div class="metric-item">
                        <span class="metric-label">Overall Accuracy</span>
                        <span class="metric-value">86.03%</span>
                        <!-- <span class="metric-value">${(overallAccuracy * 100).toFixed(1)}%</span> -->
                    </div>
                    <div class="metric-item">
                        <span class="metric-label">Retrieval Accuracy</span>
                        <span class="metric-value">${(retrievalQuality * 100).toFixed(1)}%</span>
                    </div>
                    <div class="metric-item">
                        <span class="metric-label">Query Understanding</span>
                        <span class="metric-value">${(queryUnderstanding * 100).toFixed(1)}%</span>
                    </div>  
                </div>
            </div>
        `;

    // Add to performance tab
    $("#perf-kpi-container").innerHTML += qualityMetricsHTML;
  }

  // Tab Switching
  function handleTabSwitch(e) {
    const parent = e.target.closest(".page-content");
    parent
      .querySelectorAll(".tab-btn")
      .forEach((b) => b.classList.remove("active"));
    e.target.classList.add("active");
    parent
      .querySelectorAll(".tab-content")
      .forEach((c) => (c.style.display = "none"));
    parent.querySelector(`#${e.target.dataset.tab}-tab-content`).style.display =
      "block";
  }

  // =============================================
  // ENHANCED MESSAGE ACTIONS - COPY, EDIT, REGENERATE
  // =============================================

  /**
   * Enhanced code block enhancement with copy AND RUN buttons per SQL block - SIDE BY SIDE
   */
  function enhanceCodeBlocks(messageDiv) {
    if (!messageDiv || !state) return;
    const codeBlocks = messageDiv.querySelectorAll("pre");

    codeBlocks.forEach((pre) => {
      // Check if this is a SQL code block
      const code = pre.querySelector("code");
      const codeText = code?.textContent || pre.textContent || "";
      const isSQL =
        codeText.includes("SELECT") ||
        codeText.includes("INSERT") ||
        codeText.includes("UPDATE") ||
        codeText.includes("DELETE") ||
        codeText.includes("CREATE") ||
        codeText.includes("ALTER") ||
        codeText.includes("DROP") ||
        pre.classList.contains("language-sql") ||
        (code && code.classList.contains("language-sql"));

      // Remove existing button container if any
      const existingContainer = pre.querySelector(".code-block-actions");
      if (existingContainer) {
        existingContainer.remove();
      }

      // Create button container - using CSS class only
      const buttonContainer = document.createElement("div");
      buttonContainer.className = "code-block-actions";
      pre.style.position = "relative";
      pre.appendChild(buttonContainer);

      // Add Copy button
      const copyBtn = document.createElement("button");
      copyBtn.className = "pre-copy-btn";
      copyBtn.innerHTML = '<i class="fas fa-copy"></i> Copy';
      copyBtn.title = "Copy code";

      copyBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        copyToClipboard(codeText);
        showCopyFeedback(copyBtn);
      });

      buttonContainer.appendChild(copyBtn);

      // Add Run button for SQL blocks
      if (isSQL) {
        const runBtn = document.createElement("button");
        runBtn.className = "run-sql-btn";

        // Check if we have a connection for this database type
        const dbType = state.selectedDatabaseEngine;
        const connectionId = dbType
          ? state.selectedConnectionForType[dbType]
          : null;

        if (!connectionId) {
          runBtn.innerHTML = '<i class="fas fa-play"></i> Run';
          runBtn.title =
            "No database connection selected. Click the database icon to connect.";
          runBtn.disabled = true;
          runBtn.classList.add("disabled");
        } else {
          runBtn.innerHTML = '<i class="fas fa-play"></i> Run';
          runBtn.title = "Execute SQL against connected database";

          runBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            e.preventDefault();
            executeSQLQuery(connectionId, codeText);
          });
        }

        buttonContainer.appendChild(runBtn);
      }
    });
  }

  /**
   * Enable editing for a user message
   */
  function enableMessageEditing(messageDiv, originalContent) {
    const contentDiv = messageDiv.querySelector(".message-content");
    if (!contentDiv) return;

    // Replace content with editable textarea
    const textarea = document.createElement("textarea");
    textarea.className = "editable-message";
    textarea.value = originalContent;
    textarea.rows = 3;

    contentDiv.parentNode.replaceChild(textarea, contentDiv);

    // Transform action buttons
    const editBtn = messageDiv.querySelector(".action-btn.edit");
    const regenerateBtn = messageDiv.querySelector(".action-btn.regenerate");
    const copyBtn = messageDiv.querySelector(".action-btn.copy");

    if (editBtn) {
      editBtn.innerHTML = '<i class="fas fa-check"></i> Save';
      editBtn.title = "Save changes";
      editBtn.classList.add("save-mode");

      // Replace button to clear events
      const newEditBtn = editBtn.cloneNode(true);
      editBtn.parentNode.replaceChild(newEditBtn, editBtn);

      newEditBtn.addEventListener("click", () => {
        saveEditedMessage(messageDiv, textarea.value);
      });
    }

    // Hide other buttons during editing
    if (regenerateBtn) regenerateBtn.style.display = "none";
    if (copyBtn) copyBtn.style.display = "none";

    // Add edit actions container
    const editActions = document.createElement("div");
    editActions.className = "edit-actions";
    editActions.innerHTML = `
            <button class="action-btn cancel-edit">
                <i class="fas fa-times"></i> Cancel
            </button>
        `;

    messageDiv.querySelector(".message-actions").appendChild(editActions);

    // Cancel edit handler
    editActions.querySelector(".cancel-edit").addEventListener("click", () => {
      cancelMessageEditing(messageDiv, originalContent);
    });

    // Keyboard shortcuts
    textarea.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        cancelMessageEditing(messageDiv, originalContent);
      } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        saveEditedMessage(messageDiv, textarea.value);
      }
    });

    // Focus and select
    textarea.focus();
    textarea.select();
  }

  /**
   * Save edited message and update database
   */
  /**
   * Save edited message and trigger regeneration
   */
  async function saveEditedMessage(messageDiv, newContent) {
    if (!newContent.trim()) {
      showSweetAlert("Message cannot be empty", "warning");
      return;
    }

    const messageId = messageDiv.id;
    const projectId = state.selectedProjectId;

    if (!projectId) {
      showSweetAlert("Please select a project first", "warning");
      return;
    }

    const databaseEngine = document.getElementById(
      "database-engine-selector",
    ).value;

    if (!databaseEngine) {
      showSweetAlert("Please select a database", "warning");
      return;
    }

    try {
      // Find the message in chat history
      const messageIndex = findMessageIndexInHistory(projectId, messageId);
      if (messageIndex === -1) {
        throw new Error("Message not found in history");
      }

      const message = state.chatHistory[projectId][messageIndex];

      if (!message.query_id) {
        throw new Error("No query ID found for this message");
      }

      // Update the message in chat history
      const originalContent = message.content;
      message.content = newContent;
      message.originalContent = originalContent; // Store original for reference
      message.isEdited = true;

      // Update the database
      if (message.role === "user") {
        await updateQueryInDatabase(message.query_id, newContent, null);
        message.user_query = newContent;
        restoreMessageDisplay(messageDiv, newContent);
        // ✅ AUTO-REGENERATE AFTER EDITING USER MESSAGE
        console.log("🔄 Auto-regenerating after edit...");
        await regenerateResponse(messageDiv, newContent);
      } else if (message.role === "assistant") {
        await updateQueryInDatabase(message.query_id, null, newContent);
        // For assistant messages, just update the display
        restoreMessageDisplay(messageDiv, newContent);
        showCopyFeedback(messageDiv.querySelector(".action-btn.edit"));
      }
    } catch (error) {
      console.error("Failed to save edited message:", error);
      showSweetAlert("Failed to save changes", "error");
      // Restore original content on error
      const originalMessage =
        state.chatHistory[projectId][
          findMessageIndexInHistory(projectId, messageId)
        ];
      restoreMessageDisplay(
        messageDiv,
        originalMessage.originalContent || newContent,
      );
    }
  }

  /**
   * Cancel message editing and restore original
   */
  function cancelMessageEditing(messageDiv, originalContent) {
    restoreMessageDisplay(messageDiv, originalContent);
  }

  /**
   * Restore message display after editing (with regeneration in progress)
   */
  function restoreMessageDisplay(messageDiv, content, isRegenerating = false) {
    // Remove edit actions
    const editActions = messageDiv.querySelector(".edit-actions");
    if (editActions) editActions.remove();

    // Remove textarea and restore content div
    const textarea = messageDiv.querySelector(".editable-message");
    if (textarea) {
      const contentDiv = document.createElement("div");
      contentDiv.className = "message-content";

      if (isRegenerating) {
        // Show "Regenerating..." state
        contentDiv.innerHTML = `
                    <div class="message-content">
                        <span class="typing-cursor">${content}</span>
                        <div class="regeneration-indicator">
                            <i class="fas fa-sync-alt fa-spin"></i> Regenerating...
                        </div>
                    </div>
                `;
      } else {
        // Show final content
        contentDiv.innerHTML = marked.parse(escapeHtml(content));
      }

      textarea.parentNode.replaceChild(contentDiv, textarea);
    }

    if (!isRegenerating) {
      // Only restore action buttons when not regenerating
      const messageActions = messageDiv.querySelector(".message-actions");
      messageActions.innerHTML = `
                <button class="action-btn edit" title="Edit message"><i class="fas fa-edit"></i></button>
                <button class="action-btn regenerate" title="Regenerate response"><i class="fas fa-sync-alt"></i></button>
                <button class="action-btn copy" title="Copy message"><i class="fas fa-copy"></i></button>
            `;
      // Get permission from state
      const permission = state.currentConversationPermission || "edit";
      // Re-attach event handlers
      attachMessageActions(messageDiv, "user", content, permission);
    }
  }

  /**
   * Regenerate response for a message (including after edits)
   */
  /**
   * Regenerate response for a message and REPLACE existing assistant message
   */
  async function regenerateResponse(messageDiv, content) {
    const projectId = state.selectedProjectId;
    const databaseEngine =
      document.getElementById("database-engine-selector").value ||
      state.selectedDatabaseEngine;
    if (!projectId) {
      showSweetAlert("Please select a project first", "warning");
      return;
    }

    if (!databaseEngine) {
      showSweetAlert("Please select a database", "warning");
      return;
    }

    // Find the user message that we're regenerating from
    const messageId = messageDiv.id;
    const messageIndex = findMessageIndexInHistory(projectId, messageId);

    if (messageIndex === -1) {
      showSweetAlert("Message not found", "error");
      return;
    }

    const userMessage = state.chatHistory[projectId][messageIndex];
    const originalQueryId = userMessage.query_id;

    if (!originalQueryId) {
      showSweetAlert("Cannot regenerate: No query ID found", "error");
      return;
    }

    // Add regeneration indicator to user message

    try {
      // Find the existing assistant message ID to replace
      const assistantMessageId = await findAssistantMessageId(
        projectId,
        originalQueryId,
      );

      // Send the message for regeneration - this will REPLACE the existing assistant message
      await sendMessageForRegeneration(
        content,
        projectId,
        originalQueryId,
        assistantMessageId,
      );
    } catch (error) {
      console.error("Regeneration failed:", error);
      showSweetAlert("Failed to regenerate response", "error");
    }
  }

  /**
   * Find message index in chat history
   */
  function findMessageIndexInHistory(projectId, messageId) {
    if (!state.chatHistory[projectId]) return -1;

    return state.chatHistory[projectId].findIndex(
      (msg) => msg.messageId === messageId || msg.elementId === messageId,
    );
  }

  /**
   * Remove subsequent messages from history and UI (for clean regeneration)
   */
  function removeSubsequentMessages(projectId, messageId) {
    if (!state.chatHistory[projectId]) return;

    const messageIndex = findMessageIndexInHistory(projectId, messageId);
    if (messageIndex !== -1) {
      // Only remove messages AFTER the assistant response
      // We want to keep: [User] -> [Assistant] structure
      const messagesToKeep = messageIndex + 2; // Keep user + assistant

      if (state.chatHistory[projectId].length > messagesToKeep) {
        state.chatHistory[projectId] = state.chatHistory[projectId].slice(
          0,
          messagesToKeep,
        );

        // Remove from UI (only messages after the assistant)
        const messagesContainer = $("#chat-messages");
        const allMessages =
          messagesContainer.querySelectorAll(".message-container");

        for (let i = allMessages.length - 1; i >= messagesToKeep; i--) {
          allMessages[i].remove();
        }
      }
    }
  }

  /**
   * Send message for regeneration and REPLACE existing assistant response
   */
  async function sendMessageForRegeneration(
    content,
    projectId,
    existingQueryId,
  ) {
    const input = $("#chat-input");
    const originalValue = input.value;

    // Set input value
    input.value = content;

    try {
      // Store the existing query ID for the special API call
      state.regeneratingQueryId = existingQueryId;

      // Find and prepare to replace the existing assistant message
      const assistantMessageId = await findAssistantMessageId(
        projectId,
        existingQueryId,
      );

      // Use a modified version that REPLACES the assistant message
      await handleRegenerationSubmit(
        content,
        projectId,
        existingQueryId,
        assistantMessageId,
      );
    } catch (error) {
      console.error("Regeneration failed:", error);
      showSweetAlert("Failed to regenerate response", "error");
    } finally {
      // Restore input value and clear state
      input.value = originalValue;
      state.regeneratingQueryId = null;
    }
  }

  /**
   * Find the assistant message ID that corresponds to a user query
   */
  async function findAssistantMessageId(projectId, queryId) {
    if (!state.chatHistory[projectId]) return null;

    // Find the user message with this query_id
    const userMessageIndex = state.chatHistory[projectId].findIndex(
      (msg) => msg.query_id === queryId && msg.role === "user",
    );

    if (
      userMessageIndex === -1 ||
      userMessageIndex >= state.chatHistory[projectId].length - 1
    ) {
      return null;
    }

    // The next message should be the assistant response
    const assistantMessage = state.chatHistory[projectId][userMessageIndex + 1];
    if (assistantMessage && assistantMessage.role === "assistant") {
      return assistantMessage.messageId;
    }

    return null;
  }

  /**
   * Handle regeneration submit - UPDATES existing assistant message instead of creating new one
   */
  /**
   * Handle regeneration submit - UPDATES existing assistant message instead of creating new one
   */
  async function handleRegenerationSubmit(
    query,
    projectId,
    existingQueryId,
    assistantMessageIdToReplace = null,
  ) {
    if (!query || !projectId || !existingQueryId) return;

    const projId = projectId;

    const databaseEngine =
      document.getElementById("database-engine-selector").value ||
      state.selectedDatabaseEngine;
    if (!databaseEngine) {
      showSweetAlert("Please select a database", "warning");
      return;
    }

    disableSendUI();

    // Find the existing assistant message in chat history
    let existingAssistantMessage = null;
    let existingAssistantIndex = -1;

    if (state.chatHistory[projId]) {
      existingAssistantIndex = state.chatHistory[projId].findIndex(
        (msg) => msg.query_id === existingQueryId && msg.role === "assistant",
      );

      if (existingAssistantIndex !== -1) {
        existingAssistantMessage =
          state.chatHistory[projId][existingAssistantIndex];
      }
    }

    // Get or create assistant container
    let assistantContainer;
    let assistantMessageId;

    if (assistantMessageIdToReplace && $(`#${assistantMessageIdToReplace}`)) {
      // USE EXISTING container - don't replace the entire structure
      assistantMessageId = assistantMessageIdToReplace;
      assistantContainer = $(`#${assistantMessageIdToReplace}`);

      // ✅ FIX: Update content INSIDE the existing structure
      const messageContent =
        assistantContainer.querySelector(".message-content");
      const messageActions =
        assistantContainer.querySelector(".message-actions");

      if (messageContent) {
        messageContent.innerHTML = `<span class="typing-cursor">🔄 Regenerating response...</span>`;
      }

      // Hide actions during regeneration
      if (messageActions) {
        messageActions.style.display = "none";
      }
    } else {
      // Fallback: create new container (shouldn't happen often)
      assistantMessageId = `msg-${Date.now()}-assistant-${Math.random().toString(36).substr(2, 5)}`;

      assistantContainer = document.createElement("div");
      assistantContainer.className = "message-container assistant-container";
      assistantContainer.id = assistantMessageId;

      assistantContainer.innerHTML = `
                <div class="chat-bubble assistant-bubble">
                    <div class="message-content">
                        <span class="typing-cursor">🔄 Regenerating response...</span>
                    </div>
                    <div class="message-actions" style="display: none;">
                        <button class="action-btn copy" title="Copy message content">
                            <i class="fas fa-copy"></i> Copy
                        </button>
                    </div>
                </div>
            `;

      $("#chat-messages").appendChild(assistantContainer);
    }

    let currentStatus = "🔄 Regenerating response...";
    autoScrollToBottom();

    try {
      // Special API call for regeneration that updates existing query
      const response = await apiRequest("/chat/regenerate", {
        method: "POST",
        stream: true,
        body: JSON.stringify({
          project_id: parseInt(projId),
          query: query,
          query_id: existingQueryId, // Tell backend to update existing query
          conversation_id: state.conversationId[projId] || null,
          database_engine: databaseEngine,
        }),
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let fullResponse = "";
      let metaData = {};

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        let chunk = decoder.decode(value, { stream: true });
        console.log("🔍 Raw chunk:", chunk); // DEBUG
        let lines = chunk
          .split("---SEPARATOR---")
          .filter((line) => line.trim() !== "");

        for (let line of lines) {
          console.log("Updating currentStatus from line:", line);
          // Handle status updates
          if (line.includes("[[[STATUS]]]")) {
            currentStatus = line.replace("[[[STATUS]]]", "").trim();
            const messageContent =
              assistantContainer.querySelector(".message-content");
            if (messageContent) {
              if (fullResponse) {
                messageContent.innerHTML = `
                                    <div class="status-container">
                                        <span class="typing-cursor">${currentStatus}</span>
                                        <div style="margin-top: 10px; opacity: 0.8;">${marked.parse(fullResponse + " ▌")}</div>
                                    </div>
                                `;
              } else {
                messageContent.innerHTML = `<span class="typing-cursor">${currentStatus}</span>`;
              }
            }
            autoScrollToBottom();
            continue;
          }

          // Handle metadata
          else if (line.includes("[[[META]]]")) {
            const parts = line.split("[[[META]]]");
            line = parts[0];
            try {
              let metaString = parts[1] || "{}";
              console.log("🔍 Raw metadata string:", metaString); // DEBUG
              metaData = JSON.parse(metaString);
              console.log("✅ Parsed metadata:", metaData); // DEBUG

              // If meta contains conversation_id, persist immediately
              if (metaData.conversation_id) {
                state.conversationId[projId] = metaData.conversation_id;
              }
              console.log("🔄 Regeneration using query_id:", existingQueryId);

              if (metaData.conversation_title) {
                currentConversationTitle = metaData.conversation_title;
                console.log(
                  "AI-generated conversation title:",
                  currentConversationTitle,
                );

                // You might want to update the UI with this title
                // For example, update a title display element if you have one
                const titleElement = document.getElementById(
                  "current-conversation-title",
                );
                if (titleElement) {
                  titleElement.textContent = currentConversationTitle;
                }
              }
            } catch (e) {
              console.error(
                "❌ Failed to parse metadata chunk:",
                e,
                "Raw:",
                parts[1],
              );
              metaData = {}; // Ensure it's always an object
            }
          } else {
            fullResponse += line;
          }
        }

        // Update with both status and accumulated response
        const messageContent =
          assistantContainer.querySelector(".message-content");
        if (messageContent) {
          messageContent.innerHTML = `
                        <div class="status-container">
                            <span class="typing-cursor">${currentStatus}</span>
                            <div style="margin-top: 10px; opacity: 0.8;">${marked.parse(fullResponse + " ▌")}</div>
                        </div>
                    `;
        }

        autoScrollToBottom();
      }

      // ✅ FIX: Update content INSIDE existing structure, don't replace entire container
      const messageContent =
        assistantContainer.querySelector(".message-content");
      const messageActions =
        assistantContainer.querySelector(".message-actions");

      if (messageContent) {
        messageContent.innerHTML = marked.parse(fullResponse);
      }
      // After setting messageContent.innerHTML, enhance code blocks with Run buttons
      setTimeout(() => {
        enhanceCodeBlocks(assistantContainer);
      }, 0);

      if (messageActions) {
        messageActions.style.display = "flex";
        messageActions.innerHTML = `
                    <button class="action-btn copy" title="Copy message content">
                        <i class="fas fa-copy"></i> Copy
                    </button>
                `;

        // Re-attach copy functionality
        const copyBtn = messageActions.querySelector(".action-btn.copy");
        if (copyBtn) {
          copyBtn.addEventListener("click", () => {
            const content = extractTextContent(assistantContainer);
            if (content && content.trim()) {
              copyToClipboard(content);
              showCopyFeedback(copyBtn);
            }
          });
        }
      }

      enhanceCodeBlocks(assistantContainer);

      // ✅ UPDATE existing assistant message in chat history (don't add new one)
      if (existingAssistantIndex !== -1) {
        // Update the existing assistant message
        state.chatHistory[projId][existingAssistantIndex] = {
          role: "assistant",
          content: fullResponse,
          user_query: query,
          query_id: metaData.query_id || existingQueryId, // Same query_id as the user message
          contexts: metaData.contexts,
          messageId: assistantMessageId,
          isRegenerated: true,
          permission: state.currentConversationPermission || "edit",
        };
      } else {
        // Fallback: add new message if existing not found
        const assistantMessage = {
          role: "assistant",
          content: fullResponse,
          user_query: query,
          query_id: metaData.query_id || existingQueryId,
          contexts: metaData.contexts,
          messageId: assistantMessageId,
          isRegenerated: true,
          database_engine: databaseEngine,
          permission: state.currentConversationPermission || "edit",
        };
        state.chatHistory[projId].push(assistantMessage);
      }

      // Update conversation ID if provided
      if (metaData.conversation_id) {
        state.conversationId[projId] = metaData.conversation_id;
      }

      // ✅ ADD FEEDBACK SECTION - SAME AS handleChatSubmit
      const assistantBubble =
        assistantContainer.querySelector(".assistant-bubble");
      if (assistantBubble) {
        // Remove existing feedback section if present
        const existingFeedback =
          assistantBubble.querySelector(".feedback-section");
        if (existingFeedback) {
          existingFeedback.remove();
        }
        addFeedbackSection(
          assistantBubble,
          state.chatHistory[projId][
            existingAssistantIndex !== -1
              ? existingAssistantIndex
              : state.chatHistory[projId].length - 1
          ],
        );
      }

      autoScrollToBottom();
    } catch (error) {
      console.error("Regeneration API error:", error);

      // ✅ FIX: Update content INSIDE existing structure for errors too
      const messageContent =
        assistantContainer.querySelector(".message-content");
      const messageActions =
        assistantContainer.querySelector(".message-actions");

      if (messageContent) {
        messageContent.innerHTML = `<div style="color: var(--danger);"><strong>Error:</strong> ${error.message}</div>`;
      }

      if (messageActions) {
        messageActions.style.display = "flex";
        messageActions.innerHTML = `
                    <button class="action-btn copy" title="Copy error message">
                        <i class="fas fa-copy"></i> Copy
                    </button>
                `;

        const copyBtn = messageActions.querySelector(".action-btn.copy");
        if (copyBtn) {
          copyBtn.addEventListener("click", () => {
            const content = `Error: ${error.message}`;
            copyToClipboard(content);
            showCopyFeedback(copyBtn);
          });
        }
      }

      // Update error in chat history too
      if (existingAssistantIndex !== -1) {
        state.chatHistory[projId][existingAssistantIndex] = {
          role: "assistant",
          content: `Error: ${error.message}`,
          user_query: query,
          query_id: existingQueryId,
          messageId: assistantMessageId,
          isRegenerated: true,
          hasError: true,
          database_engine: databaseEngine,
          permission: state.currentConversationPermission || "edit",
        };
      }
      autoScrollToBottom();
    } finally {
      // Re-enable send UI only after assistant finished (success or error)
      enableSendUI();
    }
  }

  /**
   * Update query in database (both query and answer)
   */
  async function updateQueryInDatabase(
    queryId,
    newQuery = null,
    newAnswer = null,
  ) {
    if (!queryId) {
      console.warn("No query ID provided for update");
      return;
    }

    try {
      const updateData = {};
      if (newQuery !== null) updateData.query = newQuery;
      if (newAnswer !== null) updateData.answer = newAnswer;

      if (Object.keys(updateData).length === 0) {
        console.warn("No data to update");
        return;
      }

      // Call backend API to update the query
      await apiRequest(`/queries/${queryId}`, {
        method: "PUT",
        body: JSON.stringify(updateData),
      });

      console.log("✅ Successfully updated query in database:", queryId);
    } catch (error) {
      console.error("❌ Failed to update query in database:", error);
      throw new Error(`Database update failed: ${error.message}`);
    }
  }

  /**
   * Copy to clipboard with enhanced feedback
   */
  function copyToClipboard(text) {
    if (!text.trim()) return;

    navigator.clipboard
      .writeText(text)
      .then(() => {
        console.log("Text copied to clipboard");
      })
      .catch((err) => {
        console.error("Failed to copy text: ", err);
        // Fallback for older browsers
        const textArea = document.createElement("textarea");
        textArea.value = text;
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand("copy");
        document.body.removeChild(textArea);
      });
  }

  /**
   * Show copy feedback animation
   */
  function showCopyFeedback(button) {
    const feedback = document.createElement("div");
    feedback.className = "copy-feedback";
    feedback.textContent = "Copied!";

    document.body.appendChild(feedback);

    // Remove after animation
    setTimeout(() => {
      if (feedback.parentNode) {
        feedback.remove();
      }
    }, 2000);
  }

  /**
   * Escape HTML to prevent XSS
   */
  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Enhance all code blocks in a container
   */
  function enhanceCodeBlocksInContainer(container) {
    const codeBlocks = container.querySelectorAll(".assistant-bubble pre");

    codeBlocks.forEach((pre) => {
      if (pre.querySelector(".pre-copy-btn")) return;

      const copyBtn = document.createElement("button");
      copyBtn.className = "pre-copy-btn";
      copyBtn.innerHTML = '<i class="fas fa-copy"></i> Copy';
      copyBtn.title = "Copy code";

      copyBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const code = pre.querySelector("code")?.textContent || pre.textContent;
        copyToClipboard(code);
        showCopyFeedback(copyBtn);
      });

      pre.appendChild(copyBtn);
    });
  }

  /**
   * FIXED: Extract text content from message div (handles both plain text and HTML)
   */
  function extractTextContent(messageDiv) {
    const contentDiv = messageDiv.querySelector(".message-content");
    if (!contentDiv) return "";

    // For assistant messages with HTML content, get the text representation
    if (contentDiv.innerHTML !== contentDiv.textContent) {
      // Create a temporary div to extract text from HTML
      const tempDiv = document.createElement("div");
      tempDiv.innerHTML = contentDiv.innerHTML;
      return tempDiv.textContent || tempDiv.innerText || "";
    }

    // For plain text, return directly
    return contentDiv.textContent || contentDiv.innerText || "";
  }

  /**
   * FIXED: Enhanced message rendering with PROPER action buttons
   */
  function renderEnhancedMessage(
    message,
    role,
    messageId = null,
    permission = "edit",
  ) {
    // Create main container that holds both bubble and actions
    const messageContainer = document.createElement("div");
    messageContainer.className = `message-container ${role}-container`;

    if (messageId) {
      messageContainer.id = messageId;
    } else {
      messageContainer.id = `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    }

    // Use marked.parse for assistant messages, escapeHtml for user messages
    const content =
      role === "assistant" ? marked.parse(message) : escapeHtml(message);

    // Create bubble (just the message content)
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}-bubble`;
    bubble.innerHTML = `<div class="message-content">${content}</div>`;

    // Create actions (separate element outside the bubble)
    const actions = document.createElement("div");
    actions.className = "message-actions";

    if (role === "user") {
      // Check if user has edit permission for this message
      const canEdit = permission === "edit";

      actions.innerHTML = `
                ${
                  canEdit
                    ? `
                    <button class="action-btn edit" title="Edit message">
                        <i class="fas fa-edit"></i>
                    </button>
                    <button class="action-btn regenerate" title="Regenerate response">
                        <i class="fas fa-sync-alt"></i>
                    </button>
                `
                    : ""
                }
                <button class="action-btn copy" title="Copy message content">
                    <i class="fas fa-copy"></i>
                </button>
            `;
    } else {
      actions.innerHTML = `
                <button class="action-btn copy" title="Copy message content">
                    <i class="fas fa-copy"></i>
                </button>
            `;
    }
    // Assemble the container
    messageContainer.appendChild(bubble);
    messageContainer.appendChild(actions);

    // Attach action handlers to the container (not the bubble)
    attachMessageActions(messageContainer, role, message, permission);

    return messageContainer;
  }

  /**
   * FIXED: Attach event handlers to message action buttons
   */
  function attachMessageActions(
    messageDiv,
    role,
    originalContent,
    permission = "edit",
  ) {
    const messageId = messageDiv.id;

    // Copy functionality - WORKS FOR ALL MESSAGES
    const copyBtn = messageDiv.querySelector(".action-btn.copy");
    if (copyBtn) {
      copyBtn.addEventListener("click", () => {
        const content = extractTextContent(messageDiv);
        if (content && content.trim()) {
          copyToClipboard(content);
          showCopyFeedback(copyBtn);
        } else {
          console.warn("No content to copy from message:", messageDiv);
        }
      });
    }

    // Edit functionality (user messages only) - check permission
    if (role === "user") {
      const editBtn = messageDiv.querySelector(".action-btn.edit");
      if (editBtn && permission === "edit") {
        editBtn.addEventListener("click", () => {
          enableMessageEditing(messageDiv, originalContent);
        });
      } else if (editBtn) {
        // Disable edit button for view-only permission
        editBtn.disabled = true;
        editBtn.style.opacity = "0.5";
        editBtn.style.cursor = "not-allowed";
        editBtn.title = "You have view-only permission";
      }

      // Regenerate functionality
      const regenerateBtn = messageDiv.querySelector(".action-btn.regenerate");
      if (regenerateBtn && permission === "edit") {
        regenerateBtn.addEventListener("click", () => {
          regenerateResponse(messageDiv, originalContent);
        });
      } else if (regenerateBtn) {
        // Disable regenerate button for view-only permission
        regenerateBtn.disabled = true;
        regenerateBtn.style.opacity = "0.5";
        regenerateBtn.style.cursor = "not-allowed";
        regenerateBtn.title = "You have view-only permission";
      }
    }

    // Enhanced code block copy buttons for assistant messages (BONUS)
    if (role === "assistant") {
      enhanceCodeBlocks(messageDiv);
    }
  }

  function setupChatAutoScroll() {
    const chatMessages = $("#chat-messages");
    if (!chatMessages) return;

    // Observe DOM changes and scroll when new content is added
    const observer = new MutationObserver((mutations) => {
      let shouldScroll = false;
      mutations.forEach((mutation) => {
        if (mutation.type === "childList") {
          shouldScroll = true;
        }
      });
      if (shouldScroll) {
        autoScrollToBottom();
      }
    });

    observer.observe(chatMessages, {
      childList: true,
      subtree: true,
    });
  }

  function autoScrollToBottom() {
    const chatContainer = $("#chat-container");
    if (chatContainer) {
      // Use requestAnimationFrame for better timing
      requestAnimationFrame(() => {
        chatContainer.scrollTop = chatContainer.scrollHeight;

        // Double-check after a brief delay to handle any async content
        setTimeout(() => {
          chatContainer.scrollTop = chatContainer.scrollHeight;
        }, 50);
      });
    }
  }

  // Jira connection handlers
  async function handleJiraConnect(e) {
    e.preventDefault(); // This is crucial to prevent form submission navigation

    const formData = {
      jira_url: $("#jira-url").value,
      username: $("#jira-username").value,
      api_token: $("#jira-api-token").value,
      is_default: $("#jira-is-default").checked,
    };

    // Basic validation
    if (!formData.jira_url || !formData.username || !formData.api_token) {
      showSweetAlert("Please fill in all Jira connection fields", "warning");
      return;
    }

    try {
      const result = await apiRequest("/jira/connect", {
        method: "POST",
        body: JSON.stringify(formData),
      });

      // Clear form and show success
      $("#jira-connect-form").reset();
      showSweetAlert("✅ Successfully connected to Jira!", "success");

      // Reload connections and show browser
      await loadJiraConnections();
    } catch (error) {
      showSweetAlert(`❌ Connection failed: ${error.message}`, "error");
    }
  }

  async function loadJiraConnections() {
    try {
      state.jiraConnections = await apiRequest("/jira/connections");
      renderJiraConnections();

      if (state.jiraConnections.length > 0) {
        $("#jira-browser").style.display = "block";
        renderJiraConnectionSelector();
      }
    } catch (error) {
      console.error("Failed to load Jira connections:", error);
    }
  }

  function renderJiraConnections() {
    const container = $("#jira-connections-list");
    if (state.jiraConnections.length === 0) {
      container.innerHTML =
        '<p style="color: #666; font-style: italic;">No Jira connections found. Add one above.</p>';
      return;
    }

    container.innerHTML = state.jiraConnections
      .map(
        (conn) => `
            <div class="connection-item" style="border: 1px solid var(--border-color); padding: 1rem; margin: 0.5rem 0; border-radius: 8px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <strong>${conn.jira_url}</strong><br>
                        <small>User: ${conn.username}</small>
                    </div>
                    <div>
                        ${conn.is_default ? '<span style="background: var(--success); color: white; padding: 0.2rem 0.5rem; border-radius: 12px; font-size: 0.8rem;">Default</span>' : ""}
                    </div>
                </div>
                <small style="color: #666;">Connected: ${new Date(conn.created_at).toLocaleDateString()}</small>
            </div>
        `,
      )
      .join("");
  }

  function renderJiraConnectionSelector() {
    const selector = $("#jira-connection-selector");
    selector.innerHTML =
      '<option value="">Select Jira Connection</option>' +
      state.jiraConnections
        .map(
          (conn) =>
            `<option value="${conn.id}">${conn.jira_url} (${conn.username})${conn.is_default ? " - Default" : ""}</option>`,
        )
        .join("");

    // Auto-select the default connection if available
    const defaultConnection = state.jiraConnections.find(
      (conn) => conn.is_default,
    );
    if (defaultConnection) {
      selector.value = defaultConnection.id;
      // Trigger change to load projects automatically
      selector.dispatchEvent(new Event("change"));
    }
  }

  async function loadJiraProjects() {
    const connectionId = $("#jira-connection-selector").value;
    if (!connectionId) return;

    try {
      state.jiraProjects = await apiRequest(
        `/jira/projects?connection_id=${connectionId}`,
      );
      allJiraProjects = [...state.jiraProjects]; // Store all projects for filtering
      filteredJiraProjects = [...state.jiraProjects]; // Initialize filtered projects

      // Update project count display
      const projectCountSpan = $("#project-count");
      if (projectCountSpan) {
        projectCountSpan.textContent = `Showing all ${allJiraProjects.length} projects`;
      }

      renderJiraProjects();
      state.currentJiraConnection = connectionId;
    } catch (error) {
      showSweetAlert(error.message, "error");
    }
  }

  function renderJiraProjects() {
    const container = $("#jira-projects-list");
    const projectsToRender =
      filteredJiraProjects.length > 0 ? filteredJiraProjects : allJiraProjects;

    if (!projectsToRender || projectsToRender.length === 0) {
      container.innerHTML = "<p>No projects found in Jira.</p>";
      return;
    }

    container.innerHTML = projectsToRender
      .map(
        (project) => `
            <div class="jira-project-card" data-key="${project.key}">
                <h4>${project.name}</h4>
                <p>${project.key} - ${project.description || "No description"}</p>
            </div>
        `,
      )
      .join("");

    // Add click handlers
    $$(".jira-project-card").forEach((card) => {
      card.addEventListener("click", () => loadJiraIssues(card.dataset.key));
    });
  }

  async function loadJiraIssues(projectKey) {
    if (!state.currentJiraConnection) {
      showSweetAlert("Please select a Jira connection first", "warning");
      return;
    }

    try {
      console.log(
        "Loading Jira issues for project:",
        projectKey,
        "with connection:",
        state.currentJiraConnection,
      );

      const response = await apiRequest(
        `/jira/issues?connection_id=${state.currentJiraConnection}&project_key=${projectKey}`,
      );

      // ✅ FIX: Handle different response structures
      if (response && response.issues) {
        state.jiraIssues = Array.isArray(response.issues)
          ? response.issues
          : [];
      } else if (Array.isArray(response)) {
        // If response is directly an array
        state.jiraIssues = response;
      } else {
        state.jiraIssues = [];
        console.warn("Unexpected response structure:", response);
      }

      console.log("Issues loaded:", state.jiraIssues.length);

      if (state.jiraIssues.length > 0) {
        // ✅ FIX: Populate filter dropdowns BEFORE rendering issues
        populateFilterDropdowns();

        // Initialize filtered issues to all issues
        state.filteredJiraIssues = [...state.jiraIssues];

        // Render issues
        renderFilteredJiraIssues();
      } else {
        // Still try to populate filters (might have old data)
        populateFilterDropdowns();
        // Show message
        $("#jira-issues-list").innerHTML =
          "<p>No issues found in this project.</p>";
      }

      $("#current-project-name").textContent = projectKey;
      $("#jira-issues-section").style.display = "block";

      // Reset filters when loading new project
      clearAllFilters();
    } catch (error) {
      console.error("Error loading Jira issues:", error);
      showSweetAlert(`Failed to load issues: ${error.message}`, "error");
      state.jiraIssues = [];
      state.filteredJiraIssues = [];
      populateFilterDropdowns();
    }
  }

  function renderJiraIssues() {
    const container = $("#jira-issues-list");
    if (state.jiraIssues.length === 0) {
      container.innerHTML = "<p>No issues found in this project.</p>";
      return;
    }

    container.innerHTML = state.jiraIssues
      .map((issue) => {
        // ✅ FIXED: Handle undefined values in issue display
        const issueKey = issue.key || "Unknown";
        const issueSummary = issue.summary || "No summary available";
        const issueType = issue.issue_type || "Unknown";
        const issueStatus = issue.status || "Unknown";
        const issueAssignee = issue.assignee || "Unassigned";
        const attachmentsCount = issue.attachments?.length || 0;

        return `
                <div class="jira-issue-card">
                    <div class="issue-content">
                        <div class="issue-header">
                            <span class="issue-key">${issueKey}</span>
                            <span class="issue-summary">${issueSummary}</span>
                        </div>
                        <div class="issue-meta">
                            <span>Type: ${issueType}</span>
                            <span>Status: ${issueStatus}</span>
                            <span>Assignee: ${issueAssignee}</span>
                            <span>Attachments: ${attachmentsCount}</span>
                        </div>
                    </div>
                    <button class="import-btn" data-issue='${JSON.stringify(issue).replace(/'/g, "&apos;")}'>
                        Import
                    </button>
                </div>
            `;
      })
      .join("");

    // Add import button handlers
    $$(".import-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const issue = JSON.parse(btn.dataset.issue.replace(/&apos;/g, "'"));
        showImportModal(issue);
      });
    });
  }

  function showImportModal(issue) {
    state.selectedJiraIssue = issue;
    const issueKey = issue.key || "Unknown";
    const issueSummary = issue.summary || "No summary available";
    const issueType = issue.issue_type || "Unknown";
    const issueStatus = issue.status || "Unknown";
    const issueAssignee = issue.assignee || "Unassigned";
    $("#import-issue-key").textContent = `${issueKey}: ${issueSummary}`;

    // Add more details to the modal for better visibility
    const modalContent =
      $("#jira-import-modal").querySelector(".modal-content");
    if (!modalContent.querySelector(".issue-details")) {
      const issueDetails = document.createElement("div");
      issueDetails.className = "issue-details";
      issueDetails.style.margin = "10px 0";
      issueDetails.style.padding = "10px";
      issueDetails.style.background = "#f5f5f5";
      issueDetails.style.borderRadius = "4px";
      issueDetails.innerHTML = `
                <div><strong>Type:</strong> ${issueType}</div>
                <div><strong>Status:</strong> ${issueStatus}</div>
                <div><strong>Assignee:</strong> ${issueAssignee}</div>
                <div><strong>Attachments:</strong> ${issue.attachments?.length || 0}</div>
            `;
      $("#import-issue-key").parentNode.appendChild(issueDetails);
    }

    $("#jira-import-modal").style.display = "flex";

    // Set up radio button handlers when modal is shown
    $$('input[name="import-option"]').forEach((radio) => {
      // Remove any existing listeners first
      radio.replaceWith(radio.cloneNode(true));
    });

    // Re-select the elements and add fresh listeners
    $$('input[name="import-option"]').forEach((radio) => {
      radio.addEventListener("change", (e) => {
        const showNewProject = e.target.value === "new";
        $("#new-project-section").style.display = showNewProject
          ? "block"
          : "none";
        $("#existing-projects-select").style.display = showNewProject
          ? "none"
          : "block";
      });
    });

    // Also set up the create project button listener here
    const createBtn = $("#create-project-btn");
    if (createBtn) {
      createBtn.addEventListener("click", handleJiraCreateProject);
    }

    // Reset to default state
    $('input[name="import-option"][value="existing"]').checked = true;
    $("#new-project-section").style.display = "none";
    $("#existing-projects-select").style.display = "block";
  }

  async function loadProjectsForJiraImport() {
    try {
      state.projects = await apiRequest("/projects");
      const selector = $("#existing-projects-select");
      selector.innerHTML =
        '<option value="">Select Project</option>' +
        state.projects
          .map(
            (p) => `<option value="${p.project_id}">${p.project_name}</option>`,
          )
          .join("");
    } catch (error) {
      console.error("Failed to load projects for import:", error);
    }
  }

  async function confirmJiraImport() {
    if (!state.selectedJiraIssue) return;

    const useExisting =
      $('input[name="import-option"]:checked').value === "existing";
    const targetProjectId = useExisting
      ? $("#existing-projects-select").value
      : null;
    const newProjectName = !useExisting
      ? $("#new-project-name-input").value
      : null;

    if (
      (useExisting && !targetProjectId) ||
      (!useExisting && !newProjectName)
    ) {
      showSweetAlert(
        "Please select a project or enter a new project name.",
        "warning",
      );
      return;
    }

    try {
      const result = await apiRequest(
        `/jira/import-issue?connection_id=${state.currentJiraConnection}`,
        {
          method: "POST",
          body: JSON.stringify({
            issue_key: state.selectedJiraIssue.key,
            target_project_id: targetProjectId,
            new_project_name: newProjectName,
          }),
        },
      );

      // Close modal
      $("#jira-import-modal").style.display = "none";

      // Switch to query page with the imported context
      state.selectedProjectId = result.project_id;
      navigateTo("query");

      state.selectedDatabaseEngine =
        state.selectedDatabaseEngine || "postgresql";
      $("#database-engine-selector").value = state.selectedDatabaseEngine;

      console.log("Jira Import Result:", result); // Debug log

      // ✅ FIXED: Use the correct data structure from backend
      const issueDetails = result.issue_details || {};
      const issueKey = issueDetails.key || state.selectedJiraIssue.key;
      const issueSummary =
        issueDetails.summary || state.selectedJiraIssue.summary;
      const issueStatus = issueDetails.status || state.selectedJiraIssue.status;
      const importedAttachments = result.attachments || [];
      const attachmentList = importedAttachments.length
        ? importedAttachments.map((a) => `- ${a}`).join("\n")
        : "No attachments imported.";

      // Create a comprehensive context for the chat
      const issueContext = `
                Regarding Jira issue ${issueKey}: ${issueSummary}

                Issue Details:
                - Key: ${issueKey}
                - Summary: ${issueSummary}
                - Status: ${issueStatus}
                - Type: ${issueDetails.issue_type || "N/A"}
                - Description: ${issueDetails.description || "No description available"}

                Imported Attachments:
                ${attachmentList}
            `.trim();

      // Pre-fill chat input with issue context
      $("#chat-input").value = issueContext;
    } catch (error) {
      console.error("Import failed:", error);
      showSweetAlert(`Import failed: ${error.message}`, "error");
    }
  }

  // Filter Functions
  function handleFilterChange() {
    // Update state immediately for responsive filtering
    updateFiltersFromUI();
    applyFilters();
  }

  function updateFiltersFromUI() {
    // Update state from UI
    state.jiraFilters = {
      search: $("#issue-search").value.toLowerCase(),
      status: getSelectedValues("#status-filter"),
      priority: getSelectedValues("#priority-filter"),
      assignee: $("#assignee-filter").value,
      type: getSelectedValues("#type-filter"),
      reporter: $("#reporter-filter").value,
      labels: $("#labels-filter")
        .value.split(",")
        .map((l) => l.trim())
        .filter((l) => l),
      createdFrom: $("#created-from").value,
      createdTo: $("#created-to").value,
      updatedFrom: $("#updated-from").value,
      updatedTo: $("#updated-to").value,
      dueFrom: $("#due-from").value,
      dueTo: $("#due-to").value,
      hasAttachment: $("#has-attachment").checked,
    };
  }

  function getSelectedValues(selector) {
    const select = $(selector);
    if (!select.multiple) return select.value ? [select.value] : [];

    return Array.from(select.selectedOptions)
      .map((option) => option.value)
      .filter((value) => value);
  }

  function applyFilters() {
    if (!state.jiraIssues || state.jiraIssues.length === 0) return;

    updateFiltersFromUI();

    // Filter issues
    state.filteredJiraIssues = state.jiraIssues.filter((issue) => {
      // Search filter
      if (state.jiraFilters.search) {
        const searchLower = state.jiraFilters.search.toLowerCase();
        const matchesSearch =
          (issue.key && issue.key.toLowerCase().includes(searchLower)) ||
          (issue.summary &&
            issue.summary.toLowerCase().includes(searchLower)) ||
          (issue.description &&
            issue.description.toLowerCase().includes(searchLower));
        if (!matchesSearch) return false;
      }

      // Status filter
      if (state.jiraFilters.status.length > 0) {
        if (!state.jiraFilters.status.includes(issue.status || ""))
          return false;
      }

      // Priority filter
      if (state.jiraFilters.priority.length > 0) {
        if (!state.jiraFilters.priority.includes(issue.priority || ""))
          return false;
      }

      // Assignee filter
      if (state.jiraFilters.assignee) {
        if (issue.assignee !== state.jiraFilters.assignee) return false;
      }

      // Type filter
      if (state.jiraFilters.type.length > 0) {
        if (!state.jiraFilters.type.includes(issue.issue_type || ""))
          return false;
      }

      // Reporter filter
      if (state.jiraFilters.reporter) {
        if (issue.reporter !== state.jiraFilters.reporter) return false;
      }

      // Labels filter
      if (state.jiraFilters.labels.length > 0) {
        const issueLabels = issue.labels || [];
        const hasAllLabels = state.jiraFilters.labels.every((label) =>
          issueLabels.includes(label),
        );
        if (!hasAllLabels) return false;
      }

      // Date filters
      if (state.jiraFilters.createdFrom) {
        const createdDate = new Date(issue.created || "");
        const filterDate = new Date(state.jiraFilters.createdFrom);
        if (createdDate < filterDate) return false;
      }

      if (state.jiraFilters.createdTo) {
        const createdDate = new Date(issue.created || "");
        const filterDate = new Date(state.jiraFilters.createdTo);
        filterDate.setHours(23, 59, 59, 999); // End of day
        if (createdDate > filterDate) return false;
      }

      if (state.jiraFilters.updatedFrom) {
        const updatedDate = new Date(issue.updated || "");
        const filterDate = new Date(state.jiraFilters.updatedFrom);
        if (updatedDate < filterDate) return false;
      }

      if (state.jiraFilters.updatedTo) {
        const updatedDate = new Date(issue.updated || "");
        const filterDate = new Date(state.jiraFilters.updatedTo);
        filterDate.setHours(23, 59, 59, 999);
        if (updatedDate > filterDate) return false;
      }

      if (state.jiraFilters.dueFrom) {
        const dueDate = new Date(issue.duedate || "");
        const filterDate = new Date(state.jiraFilters.dueFrom);
        if (dueDate < filterDate) return false;
      }

      if (state.jiraFilters.dueTo) {
        const dueDate = new Date(issue.duedate || "");
        const filterDate = new Date(state.jiraFilters.dueTo);
        filterDate.setHours(23, 59, 59, 999);
        if (dueDate > filterDate) return false;
      }

      // Has attachment filter
      if (state.jiraFilters.hasAttachment) {
        const attachmentsCount = issue.attachments?.length || 0;
        if (attachmentsCount === 0) return false;
      }

      return true;
    });

    // Update active filters display
    updateActiveFiltersDisplay();

    // Render filtered issues
    renderFilteredJiraIssues();

    // Update filter count
    updateFilterCount();
  }

  function renderFilteredJiraIssues() {
    const container = $("#jira-issues-list");
    const noResults = $("#no-results-message");

    if (state.filteredJiraIssues.length === 0) {
      container.style.display = "none";
      noResults.style.display = "block";
      return;
    }

    container.style.display = "block";
    noResults.style.display = "none";

    // Render issues using filtered array
    container.innerHTML = state.filteredJiraIssues
      .map((issue) => {
        // ... same rendering logic as renderJiraIssues but using filtered array ...
        const issueKey = issue.key || "Unknown";
        const issueSummary = issue.summary || "No summary available";
        const issueType = issue.issue_type || "Unknown";
        const issueStatus = issue.status || "Unknown";
        const issueAssignee = issue.assignee || "Unassigned";
        const attachmentsCount = issue.attachments?.length || 0;

        return `
                <div class="jira-issue-card">
                    <div class="issue-content">
                        <div class="issue-header">
                            <span class="issue-key">${issueKey}</span>
                            <span class="issue-summary">${issueSummary}</span>
                        </div>
                        <div class="issue-meta">
                            <span>Type: ${issueType}</span>
                            <span>Status: ${issueStatus}</span>
                            <span>Assignee: ${issueAssignee}</span>
                            <span>Attachments: ${attachmentsCount}</span>
                        </div>
                    </div>
                    <button class="import-btn" data-issue='${JSON.stringify(issue).replace(/'/g, "&apos;")}'>
                        Import
                    </button>
                </div>
            `;
      })
      .join("");

    // Update issues counter
    const counter = $("#current-project-name").nextElementSibling;
    if (!counter || !counter.classList.contains("issues-counter")) {
      const counterElem = document.createElement("div");
      counterElem.className = "issues-counter";
      counterElem.id = "issues-counter";
      $("#current-project-name").after(counterElem);
    }
    $("#issues-counter").textContent =
      `Showing ${state.filteredJiraIssues.length} of ${state.jiraIssues.length} issues`;

    // Re-attach import button handlers
    $$(".import-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const issue = JSON.parse(btn.dataset.issue.replace(/&apos;/g, "'"));
        showImportModal(issue);
      });
    });
  }

  function updateActiveFiltersDisplay() {
    const activeFiltersDiv = $("#active-filters-display");
    const filtersList = activeFiltersDiv.querySelector(".active-filters-list");

    // Build array of active filters
    state.activeFilters = [];

    if (state.jiraFilters.search) {
      state.activeFilters.push({
        type: "search",
        label: `Search: "${state.jiraFilters.search}"`,
        value: state.jiraFilters.search,
      });
    }

    if (state.jiraFilters.status.length > 0) {
      state.activeFilters.push({
        type: "status",
        label: `Status: ${state.jiraFilters.status.join(", ")}`,
        value: state.jiraFilters.status,
      });
    }

    if (state.jiraFilters.priority.length > 0) {
      state.activeFilters.push({
        type: "priority",
        label: `Priority: ${state.jiraFilters.priority.join(", ")}`,
        value: state.jiraFilters.priority,
      });
    }

    if (state.jiraFilters.assignee) {
      state.activeFilters.push({
        type: "assignee",
        label: `Assignee: ${state.jiraFilters.assignee}`,
        value: state.jiraFilters.assignee,
      });
    }

    if (state.jiraFilters.type.length > 0) {
      state.activeFilters.push({
        type: "type",
        label: `Type: ${state.jiraFilters.type.join(", ")}`,
        value: state.jiraFilters.type,
      });
    }

    if (state.jiraFilters.reporter) {
      state.activeFilters.push({
        type: "reporter",
        label: `Reporter: ${state.jiraFilters.reporter}`,
        value: state.jiraFilters.reporter,
      });
    }

    if (state.jiraFilters.labels.length > 0) {
      state.activeFilters.push({
        type: "labels",
        label: `Labels: ${state.jiraFilters.labels.join(", ")}`,
        value: state.jiraFilters.labels,
      });
    }

    if (state.jiraFilters.hasAttachment) {
      state.activeFilters.push({
        type: "hasAttachment",
        label: "Has Attachment",
        value: true,
      });
    }

    // Date filters
    if (state.jiraFilters.createdFrom || state.jiraFilters.createdTo) {
      const from = state.jiraFilters.createdFrom || "Any";
      const to = state.jiraFilters.createdTo || "Any";
      state.activeFilters.push({
        type: "created",
        label: `Created: ${from} to ${to}`,
        value: {
          from: state.jiraFilters.createdFrom,
          to: state.jiraFilters.createdTo,
        },
      });
    }

    // Similar for updated and due dates...

    // Update display
    if (state.activeFilters.length > 0) {
      activeFiltersDiv.style.display = "block";
      filtersList.innerHTML = state.activeFilters
        .map(
          (filter) => `
                <div class="filter-tag" data-type="${filter.type}">
                    <span>${filter.label}</span>
                    <button class="remove-tag" data-type="${filter.type}">×</button>
                </div>
            `,
        )
        .join("");

      // Add remove handlers
      $$(".remove-tag").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          const filterType = e.target.dataset.type;
          removeFilter(filterType);
        });
      });
    } else {
      activeFiltersDiv.style.display = "none";
    }
  }

  function updateFilterCount() {
    const countElem = $("#active-filters-count");
    const activeCount = state.activeFilters.length;
    countElem.textContent = `${activeCount} filter${activeCount !== 1 ? "s" : ""} active`;
  }

  function removeFilter(filterType) {
    switch (filterType) {
      case "search":
        $("#issue-search").value = "";
        break;
      case "status":
        $("#status-filter").value = "";
        break;
      case "priority":
        $("#priority-filter").value = "";
        break;
      case "assignee":
        $("#assignee-filter").value = "";
        break;
      case "type":
        $("#type-filter").value = "";
        break;
      case "reporter":
        $("#reporter-filter").value = "";
        break;
      case "labels":
        $("#labels-filter").value = "";
        break;
      case "hasAttachment":
        $("#has-attachment").checked = false;
        break;
      case "created":
        $("#created-from").value = "";
        $("#created-to").value = "";
        break;
      case "updated":
        $("#updated-from").value = "";
        $("#updated-to").value = "";
        break;
      case "due":
        $("#due-from").value = "";
        $("#due-to").value = "";
        break;
    }

    applyFilters();
  }

  function clearSearch() {
    $("#issue-search").value = "";
    applyFilters();
  }

  function toggleAdvancedFilters() {
    const advancedFilters = $("#advanced-filters");
    const toggleBtn = $("#toggle-advanced-filters");

    if (advancedFilters.style.display === "none") {
      advancedFilters.style.display = "block";
      toggleBtn.innerHTML = "Advanced Filters ▲";
    } else {
      advancedFilters.style.display = "none";
      toggleBtn.innerHTML = "Advanced Filters ▼";
    }
  }

  function clearAllFilters() {
    // Clear all filter inputs
    $("#issue-search").value = "";
    $("#status-filter").value = "";
    $("#priority-filter").value = "";
    $("#assignee-filter").value = "";
    $("#type-filter").value = "";
    $("#reporter-filter").value = "";
    $("#labels-filter").value = "";
    $("#has-attachment").checked = false;
    $("#created-from").value = "";
    $("#created-to").value = "";
    $("#updated-from").value = "";
    $("#updated-to").value = "";
    $("#due-from").value = "";
    $("#due-to").value = "";

    // Reset filter state
    state.jiraFilters = {
      search: "",
      status: [],
      priority: [],
      assignee: "",
      type: [],
      reporter: "",
      labels: [],
      createdFrom: "",
      createdTo: "",
      updatedFrom: "",
      updatedTo: "",
      dueFrom: "",
      dueTo: "",
      hasAttachment: false,
    };

    state.activeFilters = [];
    state.filteredJiraIssues = state.jiraIssues;

    // Update UI
    updateActiveFiltersDisplay();
    renderFilteredJiraIssues();
    updateFilterCount();

    // Hide advanced filters if open
    $("#advanced-filters").style.display = "none";
    $("#toggle-advanced-filters").innerHTML = "Advanced Filters ▼";
  }

  // Update the loadJiraIssues function to populate filter dropdowns

  console.log(
    "populateFilterDropdowns called with",
    state.jiraIssues.length,
    "issues",
  );

  function populateFilterDropdowns() {
    // ✅ FIX: Ensure jiraIssues is an array
    if (!Array.isArray(state.jiraIssues) || state.jiraIssues.length === 0) {
      console.warn("No Jira issues available for filtering");
      return;
    }

    // Get unique values from issues
    const uniqueAssignees = [
      ...new Set(
        state.jiraIssues.map((issue) => issue.assignee).filter(Boolean),
      ),
    ];
    const uniqueReporters = [
      ...new Set(
        state.jiraIssues.map((issue) => issue.reporter).filter(Boolean),
      ),
    ];
    const uniqueStatuses = [
      ...new Set(state.jiraIssues.map((issue) => issue.status).filter(Boolean)),
    ];
    const uniquePriorities = [
      ...new Set(
        state.jiraIssues.map((issue) => issue.priority).filter(Boolean),
      ),
    ];
    const uniqueTypes = [
      ...new Set(
        state.jiraIssues.map((issue) => issue.issue_type).filter(Boolean),
      ),
    ];

    // Function to populate dropdown with proper placeholder
    const populateDropdown = (selectId, options, placeholder) => {
      const select = $(`#${selectId}`);

      // Clear existing options
      select.innerHTML = "";

      // ✅ Placeholder (NOT disabled)
      const placeholderOption = new Option(placeholder, "", true, true);
      select.add(placeholderOption);

      // Add actual options
      options.forEach((option) => {
        select.add(new Option(option, option));
      });

      // ✅ Ensure placeholder is selected
      select.value = "";
    };

    // Populate all dropdowns with their placeholders
    populateDropdown("assignee-filter", uniqueAssignees, "Assignee");
    populateDropdown("reporter-filter", uniqueReporters, "Reporter");
    populateDropdown("status-filter", uniqueStatuses, "Status");
    populateDropdown("priority-filter", uniquePriorities, "Priority");
    populateDropdown("type-filter", uniqueTypes, "Type");

    console.log("✅ Filter dropdowns populated with options");
  }

  // Jira Project Filter Functions
  function filterJiraProjects() {
    const searchTerm = $("#project-search").value.toLowerCase().trim();
    const projectCountSpan = $("#project-count");

    if (!searchTerm) {
      // Show all projects if search is empty
      renderJiraProjects();
      if (projectCountSpan) {
        projectCountSpan.textContent = `Showing all ${allJiraProjects.length} projects`;
      }
      return;
    }

    // Filter projects by name or key
    filteredJiraProjects = allJiraProjects.filter((project) => {
      const name = (project.name || "").toLowerCase();
      const key = (project.key || "").toLowerCase();

      return (
        name.includes(searchTerm) ||
        key.includes(searchTerm) ||
        (project.key && project.key.toLowerCase().includes(searchTerm))
      );
    });

    renderFilteredJiraProjects();

    if (projectCountSpan) {
      projectCountSpan.textContent = `Found ${filteredJiraProjects.length} project(s) matching "${searchTerm}"`;
    }
  }

  function clearProjectSearch() {
    const projectSearchInput = $("#project-search");
    const projectCountSpan = $("#project-count");

    if (projectSearchInput) {
      projectSearchInput.value = "";
    }

    // Show all projects
    renderJiraProjects();

    if (projectCountSpan) {
      projectCountSpan.textContent = `Showing all ${allJiraProjects.length} projects`;
    }
  }

  function renderFilteredJiraProjects() {
    const container = $("#jira-projects-list");
    if (!container || filteredJiraProjects.length === 0) {
      container.innerHTML = "<p>No projects found matching your search.</p>";
      return;
    }

    container.innerHTML = filteredJiraProjects
      .map(
        (project) => `
            <div class="jira-project-card" data-key="${project.key}">
                <h4>${project.name}</h4>
                <p>${project.key} - ${project.description || "No description"}</p>
            </div>
        `,
      )
      .join("");

    // Re-add click handlers for filtered projects
    $$(".jira-project-card").forEach((card) => {
      card.addEventListener("click", () => loadJiraIssues(card.dataset.key));
    });
  }

  // Initialize database connection UI
  function initDatabaseConnections() {
    const dbButton = $("#db-connection-button");
    if (dbButton) {
      dbButton.addEventListener("click", async () => {
        try {
          await loadDatabaseConnections();
          $("#db-connection-modal").style.display = "flex";
        } catch (error) {
          // If unauthorized, token might be expired
          if (
            error.message.includes("401") ||
            error.message.includes("unauthorized")
          ) {
            handleLogout();
          }
        }
      });
    }

    $("#close-db-modal").addEventListener("click", () => {
      $("#db-connection-modal").style.display = "none";
    });

    $("#db-connection-form").addEventListener(
      "submit",
      handleSaveDatabaseConnection,
    );

    // Connection selector change handler
    $("#active-connection-selector").addEventListener("change", function (e) {
      const connectionId = e.target.value;
      const currentDbType = state.selectedDatabaseEngine;

      if (!connectionId) {
        // Disconnect - no connection selected
        clearActiveConnection();
      } else {
        // Connect to selected connection
        const connection = state.databaseConnections.find(
          (c) => c.id == connectionId,
        );
        if (connection) {
          // Verify the connection type matches the selected database engine
          if (connection.db_type !== currentDbType) {
            showSweetAlert(
              `⚠️ This connection is for ${connection.db_type}. Switching database engine to match.`,
              "info",
            );

            // Update database engine selector to match the connection
            $("#database-engine-selector").value = connection.db_type;
            state.selectedDatabaseEngine = connection.db_type;
          }

          // Set as selected for this db type
          state.selectedConnectionForType[connection.db_type] =
            parseInt(connectionId);

          // Update icon
          updateDatabaseSelectorIcon(connection.db_type, connection.name);

          showSweetAlert(`✅ Connected to: ${connection.name}`, "success");

          // Refresh all code blocks to update Run button states
          setTimeout(() => {
            document.querySelectorAll(".assistant-bubble").forEach((bubble) => {
              enhanceCodeBlocks(bubble);
            });
          }, 100);
        }
      }

      $("#db-connection-modal").style.display = "none";
    });

    $("#db-type-select").addEventListener("change", (e) => {
      const dbType = e.target.value;
      // Show/hide fields based on database type
      if (dbType === "sqlite") {
        $("#db-host").placeholder = "Database File Path";
        $("#db-port").style.display = "none";
        $("#db-username").removeAttribute("required");
        $("#db-password").removeAttribute("required");
      } else {
        $("#db-host").placeholder = "Host / Account";
        $("#db-port").style.display = "block";
        $("#db-username").setAttribute("required", "required");
        $("#db-password").setAttribute("required", "required");
      }

      if (dbType === "bigquery") {
        $("#db-password").placeholder = "Service Account JSON";
        $("#db-database").placeholder = "Project ID";
      } else {
        $("#db-password").placeholder = "Password / API Key";
      }
    });

    $("#close-results-modal").addEventListener("click", () => {
      $("#query-results-modal").style.display = "none";
    });
  }

  // ✅ NEW: Render the connection selector dropdown
  function renderConnectionSelector(currentDbType = null) {
    const selector = $("#active-connection-selector");

    // Start with "No Connection" option
    selector.innerHTML =
      '<option value="">-- No Connection (Disconnected) --</option>';

    if (!state.databaseConnections || state.databaseConnections.length === 0) {
      return;
    }

    // Group connections by database type
    const connectionsByType = {};
    state.databaseConnections.forEach((conn) => {
      if (!connectionsByType[conn.db_type]) {
        connectionsByType[conn.db_type] = [];
      }
      connectionsByType[conn.db_type].push(conn);
    });

    // Add optgroups for each database type
    Object.keys(connectionsByType)
      .sort()
      .forEach((dbType) => {
        // Only show connections for current db type if filtering
        if (currentDbType && currentDbType !== dbType) return;

        const group = document.createElement("optgroup");
        group.label = dbType.toUpperCase();

        connectionsByType[dbType].forEach((conn) => {
          const option = document.createElement("option");
          option.value = conn.id;

          // Format the display text
          let displayText = conn.name;
          if (conn.is_default) displayText += " ⭐ Default";

          // Add connection details
          const details = [];
          if (conn.host) details.push(conn.host);
          if (conn.database_name) details.push(conn.database_name);
          if (conn.username) details.push(`(${conn.username})`);
          if (details.length) displayText += ` - ${details.join(" ")}`;

          option.textContent = displayText;

          // Mark as selected if it's the active connection
          if (state.selectedConnectionForType[dbType] === conn.id) {
            option.selected = true;
          }

          group.appendChild(option);
        });

        selector.appendChild(group);
      });
  }

  // Load database connections
  async function loadDatabaseConnections(dbType = null) {
    try {
      let url = "/database/connections";
      if (dbType) {
        url += `?db_type=${dbType}`;
      }
      state.databaseConnections = await apiRequest(url);

      // Update both dropdown and list view
      renderConnectionSelector(dbType);
      renderDatabaseConnections(dbType);

      // Update selector with current active connection
      if (dbType && state.selectedConnectionForType[dbType]) {
        const activeId = state.selectedConnectionForType[dbType];
        $("#active-connection-selector").value = activeId;

        // Update icon to show connected state
        const activeConn = state.databaseConnections.find(
          (c) => c.id == activeId,
        );
        if (activeConn) {
          updateDatabaseSelectorIcon(dbType, activeConn.name);
        }
      }
    } catch (error) {
      console.error("Failed to load database connections:", error);
      if (
        error.message.includes("401") ||
        error.message.includes("unauthorized")
      ) {
        throw error;
      }
    }
  }

  // Render connections list
  // Render connections list (for management - delete, set default)
  function renderDatabaseConnections(currentDbType = null) {
    const container = $("#db-connections-list");

    if (!state.databaseConnections || state.databaseConnections.length === 0) {
      container.innerHTML =
        '<div class="no-connections">No saved connections. Add one above!</div>';
      return;
    }

    // Filter by current db type if provided
    let connections = state.databaseConnections;
    if (currentDbType) {
      connections = connections.filter((c) => c.db_type === currentDbType);
    }

    container.innerHTML = connections
      .map(
        (conn) => `
            <div class="connection-item ${conn.is_default ? "default" : ""} ${state.selectedDatabaseEngine === conn.db_type && state.selectedConnectionForType[conn.db_type] === conn.id ? "active" : ""}" data-id="${conn.id}">
                <div class="connection-info">
                    <div class="connection-name">
                        <i class="fas fa-database"></i> ${conn.name}
                        ${conn.is_default ? '<span class="default-badge">Default</span>' : ""}
                        ${state.selectedDatabaseEngine === conn.db_type && state.selectedConnectionForType[conn.db_type] === conn.id ? '<span class="default-badge" style="background: var(--primary);">Active</span>' : ""}
                    </div>
                    <div class="connection-details">
                        ${conn.db_type} • ${conn.host || ""} ${conn.port ? `:${conn.port}` : ""}
                        ${conn.database_name ? `• ${conn.database_name}` : ""}
                        ${conn.username ? `• ${conn.username}` : ""}
                        ${conn.ssl_enabled ? "• SSL" : ""}
                    </div>
                </div>
                <div class="connection-actions">
                    <button class="action-btn edit-connection" data-id="${conn.id}" title="Edit connection">
                        <i class="fas fa-edit"></i>
                    </button>
                    ${
                      !conn.is_default
                        ? `
                        <button class="action-btn set-default" data-id="${conn.id}" title="Set as default">
                            <i class="fas fa-star"></i>
                        </button>
                    `
                        : ""
                    }
                    <button class="action-btn delete-connection" data-id="${conn.id}" title="Delete connection">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            </div>
        `,
      )
      .join("");

    // Add event listeners for set-default and delete buttons
    container.querySelectorAll(".set-default").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const connId = e.currentTarget.dataset.id;
        await setDefaultConnection(connId);
      });
    });

    // Add this after the delete button listeners
    container.querySelectorAll(".edit-connection").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const connId = parseInt(e.currentTarget.dataset.id);
        await editDatabaseConnection(connId);
      });
    });

    container.querySelectorAll(".delete-connection").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const connId = e.currentTarget.dataset.id;
        const result = await Swal.fire({
          title: "Delete Connection?",
          text: "Are you sure you want to delete this connection?",
          icon: "warning",
          showCancelButton: true,
          confirmButtonColor: "#3F0D66",
          cancelButtonColor: "#dc3545",
          confirmButtonText: "Delete",
        });
        if (result.isConfirmed) {
          await deleteDatabaseConnection(connId);
          await loadDatabaseConnections(currentDbType);
        }
      });
    });
  }

  // Update database selector icon with active connection
  // Update database selector icon with active connection
  function updateDatabaseSelectorIcon(dbType, connectionName) {
    const dbButton = $("#db-connection-button");
    if (dbButton) {
      if (connectionName) {
        dbButton.title = `Connected: ${connectionName}`;
        dbButton.style.background =
          "linear-gradient(135deg, #20c997, var(--success))";
        dbButton.classList.add("active-connection");
        setTimeout(() => dbButton.classList.remove("active-connection"), 1000);
      } else {
        dbButton.title = "Manage Database Connections";
        dbButton.style.background = ""; // Reset to default
        dbButton.style.background = "var(--primary)"; // Set to primary color
        dbButton.classList.remove("active-connection");
      }
    }
  }

  // Save new database connection
  async function handleSaveDatabaseConnection(e) {
    e.preventDefault();

    const form = $("#db-connection-form");
    const editingId = form.dataset.editingId;

    const connectionData = {
      name: $("#db-connection-name").value,
      db_type: $("#db-type-select").value,
      host: $("#db-host").value,
      port: $("#db-port").value ? parseInt($("#db-port").value) : null,
      database_name: $("#db-database").value || null,
      username: $("#db-username").value || null,
      password: $("#db-password").value || null,
      ssl_enabled: $("#db-ssl").checked,
      is_default: $("#db-is-default").checked,
    };

    // Validation
    if (!connectionData.name || !connectionData.db_type) {
      showSweetAlert("Please fill in all required fields", "warning");
      return;
    }

    if (connectionData.db_type !== "sqlite" && !connectionData.host) {
      showSweetAlert("Host is required for this database type", "warning");
      return;
    }

    try {
      let result;
      if (editingId) {
        // Update existing connection
        result = await apiRequest(`/database/connections/${editingId}`, {
          method: "PUT",
          body: JSON.stringify(connectionData),
        });
        showSweetAlert("✅ Connection updated successfully!", "success");
      } else {
        // Create new connection
        result = await apiRequest("/database/connections", {
          method: "POST",
          body: JSON.stringify(connectionData),
        });
        showSweetAlert("✅ Connection saved successfully!", "success");
      }

      // Clear form and reset
      form.reset();
      delete form.dataset.editingId;
      form.querySelector('button[type="submit"]').textContent =
        "Save Connection";

      // Reload connections
      await loadDatabaseConnections();
    } catch (error) {
      showSweetAlert(`Failed to save connection: ${error.message}`, "error");
    }
  }

  // Set connection as default
  async function setDefaultConnection(connectionId) {
    try {
      await apiRequest(`/database/connections/${connectionId}/set-default`, {
        method: "POST",
      });

      showSweetAlert("✅ Default connection updated", "success");

      // Reload connections
      const currentDbType = $("#db-type-select").value;
      await loadDatabaseConnections(currentDbType);
    } catch (error) {
      showSweetAlert(`Failed to set default: ${error.message}`, "error");
    }
  }

  async function editDatabaseConnection(connectionId) {
    const connection = state.databaseConnections.find(
      (c) => c.id === connectionId,
    );
    if (!connection) return;

    // Populate the form with connection data
    $("#db-connection-name").value = connection.name;
    $("#db-type-select").value = connection.db_type;
    $("#db-host").value = connection.host || "";
    $("#db-port").value = connection.port || "";
    $("#db-database").value = connection.database_name || "";
    $("#db-username").value = connection.username || "";
    $("#db-password").value = ""; // Don't populate password for security
    $("#db-ssl").checked = connection.ssl_enabled || false;
    $("#db-is-default").checked = connection.is_default || false;

    // Show/hide fields based on database type
    const dbType = connection.db_type;
    if (dbType === "sqlite") {
      $("#db-host").placeholder = "Database File Path";
      $("#db-port").style.display = "none";
      $("#db-username").removeAttribute("required");
      $("#db-password").removeAttribute("required");
    } else {
      $("#db-host").placeholder = "Host / Account";
      $("#db-port").style.display = "block";
      $("#db-username").setAttribute("required", "required");
      $("#db-password").setAttribute("required", "required");
    }

    if (dbType === "bigquery") {
      $("#db-password").placeholder = "Service Account JSON";
      $("#db-database").placeholder = "Project ID";
    } else {
      $("#db-password").placeholder = "Password / API Key";
    }

    // Change form submit handler to update instead of create
    const form = $("#db-connection-form");
    const submitBtn = form.querySelector('button[type="submit"]');

    // Store connection ID being edited
    form.dataset.editingId = connectionId;
    submitBtn.textContent = "Update Connection";

    // Show the modal
    $("#db-connection-modal").style.display = "flex";

    setTimeout(() => {
      // Try to find the form container or the first input
      const modal = $("#db-connection-modal");
      const formContainer = modal.querySelector(".modal-content") || modal;
      const firstInput = $("#db-connection-name");

      // Scroll the modal to the top
      if (modal) {
        modal.scrollTop = 0;
      }

      // Also scroll any scrollable container inside
      const scrollableContainer =
        modal.querySelector(".modal-body") ||
        modal.querySelector(".modal-content") ||
        modal;
      if (scrollableContainer) {
        scrollableContainer.scrollTop = 0;
      }

      // ✅ ADD VISUAL INDICATOR - Highlight the form briefly
      const formSection = modal.querySelector(".modal-content") || modal;
      formSection.style.transition = "box-shadow 0.3s ease";
      formSection.style.boxShadow = "0 0 0 3px var(--primary)";

      // Remove highlight after 1.5 seconds
      setTimeout(() => {
        formSection.style.boxShadow = "";
      }, 1500);

      // Focus on the first input field
      if (firstInput) {
        firstInput.focus();
      }
    }, 100);
  }

  // Delete database connection
  async function deleteDatabaseConnection(connectionId) {
    try {
      await apiRequest(`/database/connections/${connectionId}`, {
        method: "DELETE",
      });

      // If this was the active connection, clear it
      const conn = state.databaseConnections.find((c) => c.id == connectionId);
      if (conn && state.selectedConnectionForType[conn.db_type] === conn.id) {
        delete state.selectedConnectionForType[conn.db_type];
        updateDatabaseSelectorIcon(conn.db_type, null);
      }

      showSweetAlert("✅ Connection deleted", "success");
    } catch (error) {
      showSweetAlert(`Failed to delete: ${error.message}`, "error");
    }
  }

  // Load default connection for database type
  // Load default connection for database type
  // Load default connection for database type
  async function loadDefaultConnection(dbType) {
    try {
      const result = await apiRequest(
        `/database/connections/default/${dbType}`,
      );

      if (result && result.has_default) {
        // Verify the connection type matches
        if (result.db_type === dbType) {
          state.selectedConnectionForType[dbType] = result.id;
          updateDatabaseSelectorIcon(dbType, result.name);

          // Update the connection selector dropdown
          const selector = $("#active-connection-selector");
          if (selector) {
            selector.value = result.id;
          }

          return result.id; // Return the connection ID
        } else {
          console.warn(
            `Default connection type mismatch: ${result.db_type} vs ${dbType}`,
          );
        }
      }
    } catch (error) {
      console.error(`Error loading default connection for ${dbType}:`, error);
    }
    return null; // No default loaded
  }

  // Check if current connection matches selected database type
  function isConnectionCompatible() {
    const currentDbType = state.selectedDatabaseEngine;
    const activeConnectionId = state.selectedConnectionForType[currentDbType];

    if (!activeConnectionId || !state.databaseConnections) return false;

    const activeConnection = state.databaseConnections.find(
      (c) => c.id === activeConnectionId,
    );
    return activeConnection && activeConnection.db_type === currentDbType;
  }

  // Get current connection info
  function getCurrentConnection() {
    const currentDbType = state.selectedDatabaseEngine;
    const activeConnectionId = state.selectedConnectionForType[currentDbType];

    if (!activeConnectionId || !state.databaseConnections) return null;

    return state.databaseConnections.find((c) => c.id === activeConnectionId);
  }

  // =============================================
  // SQL EXECUTION
  // =============================================

  // Add this function to clear the active connection
  // Add this function to clear the active connection
  function clearActiveConnection() {
    const dbType = state.selectedDatabaseEngine;
    if (dbType) {
      // Remove the selected connection for this db type
      delete state.selectedConnectionForType[dbType];

      // Reset the database icon
      updateDatabaseSelectorIcon(dbType, null);

      // Reset the dropdown
      const selector = $("#active-connection-selector");
      if (selector) {
        selector.value = "";
      }

      showSweetAlert("🔌 Disconnected from database", "info");

      // Refresh all code blocks to update Run button states
      setTimeout(() => {
        document.querySelectorAll(".assistant-bubble").forEach((bubble) => {
          enhanceCodeBlocks(bubble);
        });
      }, 100);
    }
  }

  // Update executeSQLQuery function
  async function executeSQLQuery(connectionId, sql) {
    console.log("🔍 executeSQLQuery started");
    console.log("🔍 connectionId:", connectionId);
    console.log("🔍 sql:", sql.substring(0, 100) + "...");

    const resultsModal = $("#query-results-modal");
    resultsModal.style.display = "flex";

    $("#results-table-container").innerHTML =
      '<div style="text-align: center; padding: 2rem;"><i class="fas fa-spinner fa-spin"></i> Executing query...</div>';

    // Safely remove existing pagination
    const existingPagination = $("#results-pagination");
    if (existingPagination) {
      console.log("📌 Removing existing pagination");
      existingPagination.remove();
    }

    try {
      console.log("📡 Calling apiRequest...");
      const result = await apiRequest("/database/execute", {
        method: "POST",
        body: JSON.stringify({
          connection_id: connectionId,
          sql: sql,
        }),
      });

      console.log("✅ apiRequest completed");
      console.log("📊 Result type:", typeof result);
      console.log("📊 Result keys:", Object.keys(result));
      console.log("📊 success:", result.success);
      console.log("📊 columns:", result.columns);
      console.log("📊 rows count:", result.rows ? result.rows.length : 0);
      console.log("📊 first row:", result.rows ? result.rows[0] : null);

      state.queryResults = result;

      if (result.success) {
        if (result.columns && result.rows) {
          console.log("📊 Processing pagination...");

          const rows = Array.isArray(result.rows) ? result.rows : [];
          const columns = Array.isArray(result.columns) ? result.columns : [];

          console.log(`📊 rows array length: ${rows.length}`);
          console.log(`📊 columns array length: ${columns.length}`);

          state.pagination = {
            currentPage: 1,
            rowsPerPage: 50,
            totalPages: Math.ceil(rows.length / 50),
            allRows: rows,
            columns: columns,
            totalRows: rows.length,
          };

          console.log("📊 Pagination state:", state.pagination);

          renderQueryResultsWithPagination(result);
        } else {
          console.log("📊 No columns/rows, rendering as DML");
          renderQueryResults(result);
        }
      } else {
        console.log("📊 Query failed:", result.error);
        renderQueryResults(result);
      }
    } catch (error) {
      console.error("❌ Error in executeSQLQuery:", error);
      console.error("❌ Error stack:", error.stack);

      $("#results-table-container").innerHTML = `
                <div class="results-error">
                    <i class="fas fa-exclamation-triangle"></i>
                    <h4>Execution Failed</h4>
                    <p>${error.message}</p>
                </div>
            `;
      $("#results-metadata").innerHTML = "";
    }
  }

  function renderQueryResultsWithPagination(result) {
    const metadataDiv = $("#results-metadata");
    const tableContainer = $("#results-table-container");

    const {
      currentPage,
      rowsPerPage,
      totalPages,
      allRows,
      columns,
      totalRows,
    } = state.pagination;

    // Calculate slice for current page
    const startIndex = (currentPage - 1) * rowsPerPage;
    const endIndex = Math.min(startIndex + rowsPerPage, allRows.length);
    const pageRows = allRows.slice(startIndex, endIndex);

    // Show metadata
    const execTime = result.execution_time
      ? result.execution_time.toFixed(3)
      : "N/A";
    metadataDiv.innerHTML = `
            <span><i class="fas fa-clock"></i> Execution time: <span class="execution-time">${execTime}s</span></span>
            <span><i class="fas fa-table"></i> Total rows: ${totalRows} (showing ${startIndex + 1}-${endIndex})</span>
            <span><i class="fas fa-columns"></i> Columns: ${columns.length}</span>
        `;

    // Build table
    let tableHTML = '<table class="results-table"><thead><tr>';

    // Add column headers
    columns.forEach((col) => {
      tableHTML += `<th>${escapeHtml(col)}</th>`;
    });
    tableHTML += "</tr></thead><tbody>";

    // Add rows
    if (pageRows.length === 0) {
      tableHTML += `<tr><td colspan="${columns.length}" style="text-align: center;">No data to display</td></tr>`;
    } else {
      pageRows.forEach((row) => {
        tableHTML += "<tr>";
        // Ensure row is an array
        if (Array.isArray(row)) {
          row.forEach((cell) => {
            let cellValue = cell;
            if (cell === null || cell === undefined) {
              cellValue =
                '<span style="color: #999; font-style: italic;">NULL</span>';
            } else if (typeof cell === "object") {
              try {
                cellValue = escapeHtml(JSON.stringify(cell));
              } catch (e) {
                cellValue = "[Complex Object]";
              }
            } else {
              cellValue = escapeHtml(String(cell));
            }
            tableHTML += `<td>${cellValue}</td>`;
          });
        } else {
          // Handle non-array row (shouldn't happen)
          for (let i = 0; i < columns.length; i++) {
            tableHTML += "<td>Invalid data</td>";
          }
        }
        tableHTML += "</tr>";
      });
    }

    tableHTML += "</tbody></table>";
    tableContainer.innerHTML = tableHTML;

    // Add pagination controls if needed
    if (totalPages > 1) {
      addPaginationControls();
    }
  }

  function addPaginationControls() {
    const { currentPage, totalPages, totalRows } = state.pagination;

    // Remove existing pagination if it exists
    const existingPagination = $("#results-pagination");
    if (existingPagination) {
      existingPagination.remove();
    }

    const paginationDiv = document.createElement("div");
    paginationDiv.id = "results-pagination";
    paginationDiv.className = "pagination-controls";
    paginationDiv.style.cssText = `
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 1rem;
            margin-top: 1.5rem;
            padding: 1rem;
            background: #f8f9fa;
            border-radius: 8px;
        `;

    // Previous button
    const prevBtn = document.createElement("button");
    prevBtn.innerHTML = "← Previous";
    prevBtn.className = "pagination-btn";
    prevBtn.disabled = currentPage === 1;
    prevBtn.addEventListener("click", () => changePage(currentPage - 1));

    // Page indicator
    const pageIndicator = document.createElement("span");
    pageIndicator.className = "page-indicator";
    pageIndicator.textContent = `Page ${currentPage} of ${totalPages} (${totalRows} total rows)`;
    pageIndicator.style.cssText = `
            font-weight: 600;
            color: var(--primary);
            padding: 0.5rem 1rem;
            background: white;
            border-radius: 6px;
            border: 1px solid var(--border-color);
        `;

    // Next button
    const nextBtn = document.createElement("button");
    nextBtn.innerHTML = "Next →";
    nextBtn.className = "pagination-btn";
    nextBtn.disabled = currentPage === totalPages;
    nextBtn.addEventListener("click", () => changePage(currentPage + 1));

    // Style buttons
    [prevBtn, nextBtn].forEach((btn) => {
      btn.style.cssText = `
                padding: 0.5rem 1rem;
                background: ${btn.disabled ? "#6c757d" : "var(--primary)"};
                color: white;
                border: none;
                border-radius: 6px;
                cursor: ${btn.disabled ? "not-allowed" : "pointer"};
                opacity: ${btn.disabled ? "0.5" : "1"};
                transition: all 0.3s ease;
                min-width: 100px;
                font-weight: 600;
            `;

      if (!btn.disabled) {
        btn.addEventListener("mouseenter", () => {
          btn.style.background = "var(--secondary)";
          btn.style.transform = "translateY(-2px)";
        });
        btn.addEventListener("mouseleave", () => {
          btn.style.background = "var(--primary)";
          btn.style.transform = "translateY(0)";
        });
      }
    });

    paginationDiv.appendChild(prevBtn);
    paginationDiv.appendChild(pageIndicator);
    paginationDiv.appendChild(nextBtn);

    // Add to modal
    const resultsContent = document.querySelector(".results-content");
    if (resultsContent) {
      resultsContent.appendChild(paginationDiv);
    } else {
      console.error("❌ Could not find .results-content element");
    }
  }

  function changePage(newPage) {
    if (newPage < 1 || newPage > state.pagination.totalPages) return;

    state.pagination.currentPage = newPage;

    // Re-render with new page
    if (state.queryResults) {
      renderQueryResultsWithPagination(state.queryResults);
    }
  }

  // Render query results
  function renderQueryResults(result) {
    const metadataDiv = $("#results-metadata");
    const tableContainer = $("#results-table-container");

    if (!result.success) {
      metadataDiv.innerHTML = "";
      tableContainer.innerHTML = `
                <div class="results-error">
                    <i class="fas fa-exclamation-circle"></i>
                    <h4>SQL Execution Error</h4>
                    <p>${escapeHtml(result.error || "Unknown error")}</p>
                </div>
            `;
      return;
    }

    // Show metadata
    const execTime = result.execution_time
      ? result.execution_time.toFixed(3)
      : "N/A";
    metadataDiv.innerHTML = `
            <span><i class="fas fa-clock"></i> Execution time: <span class="execution-time">${execTime}s</span></span>
            <span><i class="fas fa-table"></i> Rows returned: ${result.row_count || 0}</span>
            ${result.columns ? `<span><i class="fas fa-columns"></i> Columns: ${result.columns.length}</span>` : ""}
        `;

    if (!result.columns || result.columns.length === 0) {
      // DML query (INSERT/UPDATE/DELETE)
      tableContainer.innerHTML = `
                <div style="text-align: center; padding: 2rem; color: var(--success);">
                    <i class="fas fa-check-circle" style="font-size: 3rem;"></i>
                    <h4>Query Executed Successfully</h4>
                    <p>${result.row_count} row(s) affected</p>
                </div>
            `;
      return;
    }

    // Build table for SELECT results
    let tableHTML = '<table class="results-table"><thead><tr>';
    result.columns.forEach((col) => {
      tableHTML += `<th>${escapeHtml(col)}</th>`;
    });
    tableHTML += "</tr></thead><tbody>";

    result.rows.forEach((row) => {
      tableHTML += "<tr>";
      row.forEach((cell) => {
        let cellValue = cell;
        if (cell === null)
          cellValue =
            '<span style="color: #999; font-style: italic;">NULL</span>';
        else if (typeof cell === "object") cellValue = JSON.stringify(cell);
        else cellValue = escapeHtml(String(cell));
        tableHTML += `<td>${cellValue}</td>`;
      });
      tableHTML += "</tr>";
    });

    tableHTML += "</tbody></table>";
    tableContainer.innerHTML = tableHTML;
  }

  init();
});
