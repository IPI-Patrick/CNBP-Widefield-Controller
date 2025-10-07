import plotly.express as px
import numpy as np
import plotly.graph_objects as go
import os

# Load .npz file from ./Experiments
npz_files = [f for f in os.listdir('./Experiments') if f.endswith('.npz')]
if not npz_files:
    print("No .npz files found in ./Experiments")
else:
    npz_path = os.path.join('./Experiments', npz_files[0])
    print(f"Loading: {npz_path}")
    data = np.load(npz_path)
    print("Keys in .npz file:", data.files)
    for key in data.files:
        arr = data[key]
        print(f"{key}: shape={arr.shape}, dtype={arr.dtype}")

    print(data['frames'])
    # Plot the first acquisition frame if available
    fig = go.Figure()
    fig.add_trace( go.Scatter(x=data['pH_frames'], y=data['pH_values'], mode='lines+markers', name='pH Values') )
    fig.add_trace( go.Scatter(x=data['frames'], y=np.mean(data['acquisitions'], axis=(1,2)), mode='lines+markers', name='Acquisitions') )
    fig.show()
