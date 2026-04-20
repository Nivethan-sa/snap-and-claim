from graphviz import Digraph

# --- 1. Initialize the Diagram ---
flow = Digraph('System_Flow', filename='snap_claim_system_flow')
flow.attr(rankdir='TB', )
flow.attr('node', shape='box', style='rounded')

# --- 2. Define Nodes ---
# Start and End points
flow.node('start', 'Start', shape='oval', style='filled', fillcolor='lightgreen')
flow.node('end', 'End', shape='oval', style='filled', fillcolor='lightcoral')

# Claimant (User) Path
flow.node('login', 'Claimant Registers or Logs In')
flow.node('submit', 'Claimant Fills & Submits Claim Form\nwith Image')
flow.node('save_claim', 'System Saves Claim to Database\n(Status: Submitted)')

# Admin Path
flow.node('admin_login', 'Admin Logs In to Dashboard')
flow.node('view_claims', 'Admin Views Pending Claims')
flow.node('select_claim', 'Admin Selects Claim for Review')
flow.node('run_analysis', 'Admin Runs Forensic Analysis')

# Decision and Final Steps
flow.node('decision', 'Is Claim Authentic?', shape='diamond', style='filled', fillcolor='lightyellow')
flow.node('approve', 'Admin Approves Claim')
flow.node('disapprove', 'Admin Disapproves Claim')
flow.node('update_status', 'System Updates Claim Status\nin Database')
flow.node('view_status', 'Claimant Views Updated Status')

# --- 3. Define Edges (Flow) ---
flow.edge('start', 'login')
flow.edge('login', 'submit')
flow.edge('submit', 'save_claim')
flow.edge('save_claim', 'admin_login')
flow.edge('admin_login', 'view_claims')
flow.edge('view_claims', 'select_claim')
flow.edge('select_claim', 'run_analysis')
flow.edge('run_analysis', 'decision')

flow.edge('decision', 'approve', label='  Yes')
flow.edge('decision', 'disapprove', label='  No')

flow.edge('approve', 'update_status')
flow.edge('disapprove', 'update_status')

flow.edge('update_status', 'view_status')
flow.edge('view_status', 'end')


# --- 4. Render the Final Image ---
try:
    flow.render(view=False, format='png')
    print("Successfully generated 'snap_claim_system_flow.png'")
except Exception as e:
    print(f"Error: Could not render the diagram. Ensure Graphviz is installed and in your system's PATH.")
    print(f"Details: {e}")