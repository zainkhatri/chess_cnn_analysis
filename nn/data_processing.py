import os
import json
import numpy as np
import torch
from torch.utils.data import DataLoader
import chess
import chess.pgn
import chess.engine
from tqdm import tqdm
import glob
from model import ChessDataset, encode_board, move_to_index

def process_pgn_file(pgn_file, stockfish_path, max_games=1000, min_elo=1800):
    """Process a PGN file and extract positions with evaluations"""
    positions = []
    evals = []
    best_moves = []
    
    # Init Stockfish 
    try:
        engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
        engine.configure({"Threads": 2})  # Save resources
        print(f"Successfully loaded Stockfish from {stockfish_path}")
        print(f"Processing {pgn_file}")
    except Exception as e:
        print(f"Stockfish error: {e}, skipping evaluations")
        engine = None
    
    # Parse games
    with open(pgn_file, 'r') as f:
        game_count = 0
        
        while game_count < max_games:
            try:
                game = chess.pgn.read_game(f)
                if game is None:
                    break
                
                # Check ratings
                white_elo = int(game.headers.get("WhiteElo", 0))
                black_elo = int(game.headers.get("BlackElo", 0))
                
                if white_elo >= min_elo and black_elo >= min_elo:
                    board = game.board()
                    
                    moves_played = 0
                    for move in game.mainline_moves():
                        # Sample strategically
                        if moves_played >= 10 and moves_played % 3 == 0:
                            # Get position
                            fen = board.fen()
                            
                            # Get evaluation
                            eval_score = 0
                            if engine:
                                try:
                                    # Deeper analysis
                                    info = engine.analyse(board, chess.engine.Limit(depth=15), multipv=1)
                                    eval_score = info["score"].white().score(mate_score=10000) / 100.0  # To pawns
                                    
                                    # Get best moves
                                    top_moves = engine.analyse(board, chess.engine.Limit(depth=15), multipv=3)
                                    best_move = top_moves[0]["pv"][0]
                                    
                                    # Store data
                                    encoded_board = encode_board(fen)
                                    positions.append(encoded_board)
                                    evals.append(eval_score)
                                    
                                    # Best move index
                                    move_idx = move_to_index(best_move.uci())
                                    best_moves.append(move_idx)
                                    
                                except Exception as e:
                                    print(f"Evaluation error: {e}")
                                    # Use actual move
                                    encoded_board = encode_board(fen)
                                    positions.append(encoded_board)
                                    evals.append(0)  # Neutral
                                    
                                    # Actual move
                                    move_idx = move_to_index(move.uci())
                                    best_moves.append(move_idx)
                            else:
                                # No engine fallback
                                encoded_board = encode_board(fen)
                                positions.append(encoded_board)
                                evals.append(0)  # Neutral
                                
                                # Actual move
                                move_idx = move_to_index(move.uci())
                                best_moves.append(move_idx)
                        
                        # Make move
                        board.push(move)
                        moves_played += 1
                    
                    game_count += 1
                    if game_count % 10 == 0:
                        print(f"Processed {game_count} games from {pgn_file}")
            except Exception as e:
                print(f"Error processing game: {e}")
                continue
    
    if engine:
        engine.quit()
    
    print(f"Processed {game_count} games, extracted {len(positions)} positions from {pgn_file}")
    
    return positions, evals, best_moves

def process_pgn_files(pgn_files, stockfish_path, max_games_per_file=100, min_elo=1800):
    """Process multiple PGN files sequentially"""
    print(f"Processing {len(pgn_files)} PGN files with Stockfish for high-quality evaluations")
    
    # Collect data
    all_positions = []
    all_evals = []
    all_moves = []
    
    for pgn_file in tqdm(pgn_files, desc="Processing PGN files"):
        positions, evals, moves = process_pgn_file(pgn_file, stockfish_path, max_games_per_file, min_elo)
        all_positions.extend(positions)
        all_evals.extend(evals)
        all_moves.extend(moves)
    
    return all_positions, all_evals, all_moves

def prepare_dataset(positions, evals, moves, test_split=0.2, batch_size=64):
    """Prepare torch datasets and dataloaders"""
    # To tensors
    positions_tensor = torch.stack(positions)
    evals_tensor = torch.tensor(evals, dtype=torch.float32).unsqueeze(1)  # Add dimension
    moves_tensor = torch.tensor(moves, dtype=torch.long)
    
    # Split data
    num_samples = len(positions)
    num_test = int(num_samples * test_split)
    num_train = num_samples - num_test
    
    # Shuffle indices
    indices = np.random.permutation(num_samples)
    train_indices = indices[:num_train]
    test_indices = indices[num_train:]
    
    # Create datasets
    train_dataset = ChessDataset(
        positions_tensor[train_indices], 
        evals_tensor[train_indices], 
        moves_tensor[train_indices]
    )
    
    test_dataset = ChessDataset(
        positions_tensor[test_indices], 
        evals_tensor[test_indices], 
        moves_tensor[test_indices]
    )
    
    # Create loaders
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=2)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False, num_workers=2)
    
    return train_loader, test_loader

def main():
    """Main function to process PGN files"""
    # Setup paths
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    processed_data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'nn', 'processed_data')
    os.makedirs(processed_data_dir, exist_ok=True)
    
    # Find PGNs
    pgn_files = glob.glob(os.path.join(data_dir, '*.pgn'))
    
    if not pgn_files:
        print(f"No PGN files found in {data_dir}")
        return
    
    print(f"Found {len(pgn_files)} PGN files")
    
    # Find Stockfish
    stockfish_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'public', 'Stockfish')
    
    if not os.path.exists(stockfish_path):
        print(f"Stockfish not found at {stockfish_path}")
        print("Looking for stockfish.js instead...")
        stockfish_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'public', 'stockfish.js')
    
    # Process files
    positions, evals, moves = process_pgn_files(
        pgn_files[:5],  # First 5
        stockfish_path,
        max_games_per_file=150,  # More games
        min_elo=1800  # Quality filter
    )
    
    print(f"Total positions collected: {len(positions)}")
    
    if len(positions) == 0:
        print("No positions extracted. Check your PGN files and stockfish installation.")
        return
    
    # Save data
    output_file = os.path.join(processed_data_dir, 'processed_data_high_quality.pt')
    
    torch.save({
        'positions': torch.stack(positions),
        'evals': torch.tensor(evals),
        'moves': torch.tensor(moves)
    }, output_file)
    
    print(f"Saved processed data to {output_file}")
    
    # Create sample
    if len(positions) > 1000:
        sample_indices = np.random.choice(len(positions), 1000, replace=False)
        sample_positions = [positions[i] for i in sample_indices]
        sample_evals = [evals[i] for i in sample_indices]
        sample_moves = [moves[i] for i in sample_indices]
        
        torch.save({
            'positions': torch.stack(sample_positions),
            'evals': torch.tensor(sample_evals),
            'moves': torch.tensor(sample_moves)
        }, os.path.join(processed_data_dir, 'sample_high_quality.pt'))
        
        print(f"Saved sample data with 1000 positions")

if __name__ == "__main__":
    main()