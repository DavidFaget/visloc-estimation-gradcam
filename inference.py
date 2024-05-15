import argparse
import h5py
import torch
import pickle
import requests
import numpy as np
import cv2
import matplotlib.pyplot as plt
from PIL import Image
from src import utils, modules


def evaluate(args):
    print("\n> loading model")
    model = modules.GeoLocModel(pretrained=True).eval()
    if not args.use_cpu:
        model = model.to(args.gpu)
    transform = utils.Preprocessing("inference", backbone="efficientnet")

    mus = model.cls_head.mus.t().detach().cpu().numpy()
    cells_assignments = pickle.load(open("data/cells_assignments.pkl", "rb"))

    print("\n> loading background collection")
    with h5py.File(args.background, "r", driver=None) as hdf5_file:
        back_col_emb = hdf5_file["features"][:, :].T.astype(np.float32)
        back_col_cells = hdf5_file["labels"][:, :]
    #print("Background collection features:", back_col_emb.T.shape)
    #print("Background collection cells:", back_col_cells.shape)

    print("\n> loading image")
    if args.image_path is None:
        img = requests.get(args.image_url, stream=True).raw
    else:
        img = args.image_path
    query_image = Image.open(img).convert("RGB")
    query_tensor = transform(query_image)
    #print("Image tensor shape:", query_tensor.detach().numpy().shape)
    
    # Save the original image
    plt.figure(figsize=(8, 6))
    plt.imshow(query_image)
    plt.savefig("/content/figure_ori.png")
    
    print("\n> run inference")
    if not args.use_cpu:
        query_tensor = query_tensor.to(args.gpu)
    prediction, cell_probs, embeddings, _ = model(query_tensor.unsqueeze(0))
    embeddings = embeddings.detach().cpu().numpy()
    cell_probs = cell_probs[0]
    max_cell = torch.argmax(cell_probs)
    max_cell = int(max_cell.item())

    # Search within Cell scheme
    if max_cell in cells_assignments:
        idxs = np.array(list(cells_assignments[max_cell]))
        sims = np.dot(embeddings, back_col_emb[:, idxs])[0]

        NNs = np.argsort(-sims)[: args.top_k]
        sims = sims[NNs]
        candidates = back_col_cells[idxs[NNs]]
        pr = utils.spatial_clustering(candidates, sims, radius=args.eps, a=0)
    else:
        pr = prediction[0].detach().cpu().numpy()

    conf = utils.prediction_density(max_cell, cell_probs, mus)
    
    if args.gradcam:
        grad_cam = modules.GradCAM(model)
        cam = grad_cam.generate_cam(query_tensor, max_cell)

        # Normalize CAM for visualization
        cam = cam - cam.min()  # Translate to have minimum at 0
        cam = cam / cam.max()  # Scale to have maximum at 1
        
        # Convert to 8-bit image (values from 0 to 255)
        cam_2 = np.uint8(255 * cam.detach().cpu().numpy())
        
        # Apply color map directly on the already converted cam_2
        heatmap = cv2.applyColorMap(cam_2, cv2.COLORMAP_JET)
        heatmap = cv2.cvtColor(heatmap, cv2.COLOR_BGR2RGB)
        
        query_tensor_np = query_tensor.cpu().numpy()  # Convert to numpy array 
        query_tensor_np = np.transpose(query_tensor_np, (1, 2, 0)) 
        
        query_tensor_np = np.uint8(255 * (query_tensor_np - query_tensor_np.min()) / (query_tensor_np.max() - query_tensor_np.min()))
        
        # Ensure both images are of type np.uint8, if not already
        query_tensor_np = query_tensor_np.astype(np.uint8)
        
        # Display the heatmap
        plt.figure(figsize=(8, 6))
        plt.imshow(heatmap) 
        plt.savefig("/content/heatmap.png")
        
        # Use cv2.addWeighted to blend images
        try:
            final_image = cv2.addWeighted(heatmap, 0.5, query_tensor_np, 0.5, 0)
        except cv2.error as e:
            print("Error in cv2.addWeighted:", e)
            # Check dimensions and data types for further debugging
            print("heatmap shape and dtype:", heatmap.shape, heatmap.dtype)
            print("query_tensor_np shape and dtype:", query_tensor_np.shape, query_tensor_np.dtype)
        
        # Display the final image
        plt.figure(figsize=(8, 6))
        plt.imshow(final_image)
        plt.axis('off')
        plt.savefig("/content/figure.png")
    
    print("Prediction (Lat,Lon): ({:.4f}, {:.4f})".format(*pr))
    print("Confidence: {:.1f}%".format(conf[args.conf_scale] * 100))



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="")
    parser.add_argument("-ip", "--image_path", type=str, default=None)
    parser.add_argument("-iu", "--image_url", type=str, default=None)
    parser.add_argument(
        "-b", "--background", type=str, default="./back_coll_features.hdf5"
    )
    parser.add_argument("-g", "--gpu", type=int, default=0)
    parser.add_argument("-k", "--top_k", type=int, default=10)
    parser.add_argument("-e", "--eps", type=float, default=1.0)
    parser.add_argument("-cpu", "--use_cpu", action="store_true")
    parser.add_argument("-cs", "--conf_scale", type=int, default=25)
    parser.add_argument("-gradcam", "--gradcam", action="store_true")
    args = parser.parse_args()

    if args.image_path is None and args.image_url is None:
        raise Exception("Please provide an image path or URL as input")

    evaluate(args)
